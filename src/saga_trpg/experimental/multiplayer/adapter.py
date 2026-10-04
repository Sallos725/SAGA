"""In-process MCP-style player capabilities, bound by trusted host configuration.

This is a DTO boundary, not a Python sandbox or an authenticated network server.
The repository, full manifest and channel must never be given to a model driver.
"""

import sqlite3

from saga_trpg.adapters.storage.database import RepositoryVersionError
from saga_trpg.adapters.storage.sqlite import RepositoryConflict, SQLiteWorldRepository
from saga_trpg.experimental.multiplayer.models import (
    ActionProposal,
    ActionReceipt,
    ActionSpec,
    ActorView,
    CampaignSession,
    EventPage,
    MemoryQuery,
    MemoryRetrieval,
    MessageDraft,
    PartyMessage,
    RecalledMemory,
    VisibleActor,
)
from saga_trpg.experimental.multiplayer.session import AdmissionError, action_event, participant_for
from saga_trpg.experimental.multiplayer.visibility import project_events, project_state
from saga_trpg.ports.memory import MemoryPort
from saga_trpg.ports.tools import ToolExecutionPending, ToolIdentityConflict
from saga_trpg.runtime.engine import IdempotencyConflict
from saga_trpg.runtime.hashing import stable_value_hash
from saga_trpg.runtime.replay import ReplayError


class PartyChannel:
    """Explicit benchmark communication, retained in run artifacts, never in WorldState."""

    def __init__(self) -> None:
        self.messages: list[PartyMessage] = []


class PlayerAdapter:
    capabilities = (
        "get_my_state",
        "get_visible_state",
        "get_available_actions",
        "get_recent_visible_events",
        "recall_memory",
        "propose_action",
        "inspect_target",
        "send_party_message",
    )

    def __init__(
        self,
        repository: SQLiteWorldRepository,
        session: CampaignSession,
        participant_id: str,
        *,
        channel: PartyChannel | None = None,
        memory: MemoryPort | None = None,
    ) -> None:
        self._repository = repository
        self._session = CampaignSession.model_validate_json(session.model_dump_json())
        self._participant = participant_for(self._session, participant_id)
        reference = repository.ruleset_for_world(session.world_id)
        fingerprint = stable_value_hash(session.model_dump(mode="json", exclude={"branch_id"}))
        if (
            not reference.ruleset_id.startswith("experiment.multiplayer/")
            or reference.ruleset_version.rsplit("/", 1)[-1] != fingerprint
        ):
            raise RepositoryVersionError("session manifest differs from the pinned policy")
        self._channel = channel if channel is not None else PartyChannel()
        self._memory = memory
        self.memory_retrievals: list[MemoryRetrieval] = []

    def get_visible_state(self) -> ActorView:
        if "read" not in self._participant.permissions:
            raise AdmissionError("forbidden")
        state = self._repository.get_state(
            self._session.world_id, branch_id=self._session.branch_id
        )
        return project_state(state, self._session, self._participant)

    def get_my_state(self) -> ActorView:
        return self.get_visible_state()

    def get_available_actions(self) -> tuple[ActionSpec, ...]:
        return self.get_visible_state().available_actions

    def inspect_target(self, actor_id: str) -> VisibleActor | None:
        return next(
            (actor for actor in self.get_visible_state().actors if actor.actor_id == actor_id), None
        )

    def get_recent_visible_events(self, *, after_seq: int = 0) -> EventPage:
        if "read" not in self._participant.permissions:
            raise AdmissionError("forbidden")
        if type(after_seq) is not int or after_seq < 0:
            raise AdmissionError("invalid_action")
        entries = self._repository.entries(
            self._session.world_id, branch_id=self._session.branch_id
        )
        return project_events(entries, self._participant, after_seq)

    def propose_action(self, proposed: ActionProposal) -> ActionReceipt:
        try:
            proposal = ActionProposal.model_validate(proposed.model_dump())
        except ValueError:
            return ActionReceipt(accepted=False, code="invalid_action")
        if (
            proposal.participant_id != self._participant.participant_id
            or proposal.actor_id != self._participant.actor_id
            or "propose" not in self._participant.permissions
        ):
            return ActionReceipt(accepted=False, code="forbidden")
        try:
            outcome = self._repository.dispatch(
                self._session.world_id,
                action_event(proposal),
                branch_id=self._session.branch_id,
            )
        except AdmissionError as exc:
            return ActionReceipt(accepted=False, code=str(exc))
        except (IdempotencyConflict, ToolIdentityConflict):
            return ActionReceipt(accepted=False, code="conflict")
        except (ToolExecutionPending, RepositoryConflict):
            return ActionReceipt(accepted=False, code="pending")
        except (OSError, sqlite3.Error, ReplayError, RepositoryVersionError):
            return ActionReceipt(accepted=False, code="unavailable")
        except (ValueError, KeyError, TypeError):
            return ActionReceipt(accepted=False, code="invalid_action")
        except Exception:
            # External tools can fail with arbitrary provider messages. Their durable
            # pending claim remains intact, and no exception text becomes player context.
            return ActionReceipt(accepted=False, code="unavailable")
        return ActionReceipt(
            accepted=True,
            code="accepted",
            replayed=outcome.replayed,
            event_id=outcome.entry.committed.event.event_id,
            seq=outcome.entry.committed.seq,
        )

    def send_party_message(self, draft: MessageDraft) -> PartyMessage:
        if "message" not in self._participant.permissions or self._participant.actor_id is None:
            raise AdmissionError("forbidden")
        draft = MessageDraft.model_validate(draft.model_dump())
        view = self.get_visible_state()
        recipients = tuple(p.actor_id for p in self._session.participants if p.actor_id is not None)
        message = PartyMessage(
            world_id=view.world_id,
            branch_id=view.branch_id,
            sender_actor_id=self._participant.actor_id,
            recipients=recipients,
            text=draft.text,
            claims=draft.claims,
            message_seq=len(self._channel.messages) + 1,
            after_seq=view.seq,
        )
        self._channel.messages.append(message)
        return self._project_message(message)

    def _project_message(self, message: PartyMessage) -> PartyMessage:
        return message.model_copy(
            update={
                "recipients": tuple(
                    actor
                    for actor in message.recipients
                    if actor in self._participant.visible_actor_ids
                ),
            },
            deep=True,
        )

    def get_party_messages(self) -> tuple[PartyMessage, ...]:
        if "read" not in self._participant.permissions:
            raise AdmissionError("forbidden")
        return tuple(
            self._project_message(message)
            for message in self._channel.messages
            if message.world_id == self._session.world_id
            and message.branch_id == self._session.branch_id
            and self._participant.actor_id in message.recipients
            and message.sender_actor_id in self._participant.visible_actor_ids
        )

    async def recall_memory(self, query: str) -> tuple[RecalledMemory, ...]:
        view = self.get_visible_state()
        if view.actor_id is None:
            raise AdmissionError("forbidden")
        events = self.get_recent_visible_events().events
        permitted_ids = {event.event_id for event in events if event.seq <= view.seq}
        request = MemoryQuery(
            world_id=view.world_id,
            branch_id=view.branch_id,
            actor_id=view.actor_id,
            as_of_event=next((e.event_id for e in reversed(events) if e.seq <= view.seq), None),
            query=query,
        )
        status = "disabled"
        approved = []
        if self._memory is not None:
            try:
                hits = await self._memory.retrieve(
                    world_id=view.world_id,
                    actor_scope=view.actor_id,
                    query=query,
                    limit=8,
                )
                for hit in hits[:8]:
                    metadata = hit.metadata
                    if (
                        metadata.get("world_id") == view.world_id
                        and metadata.get("branch_id") == view.branch_id
                        and metadata.get("actor_scope") == view.actor_id
                        and metadata.get("event_id") in permitted_ids
                        and isinstance(metadata.get("memory_id"), str)
                    ):
                        approved.append(
                            RecalledMemory(
                                memory_id=metadata["memory_id"],
                                event_id=metadata["event_id"],
                                text=hit.text,
                            )
                        )
                status = "ok"
            except Exception:
                status = "failed"
                approved = []
        result = MemoryRetrieval(query=request, status=status, hits=tuple(approved))
        self.memory_retrievals.append(result)
        return result.hits
