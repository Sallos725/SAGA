"""Versioned, no-memory PocketRisu bridge; all effects go through the repository."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from saga_trpg.adapters.intent.vertex import (
    PENDING,
    TARGET_ALIASES,
    VertexIntentTool,
    event_from_result,
)
from saga_trpg.adapters.narrator.fallback import FallbackNarrator
from saga_trpg.adapters.narrator.vertex import VertexNarrator
from saga_trpg.adapters.storage.sqlite import (
    EventLocation,
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import WorldState
from saga_trpg.domain.tools import ToolCall
from saga_trpg.modules.srd_5_2_1.rules import ROLL_ACTIONS
from saga_trpg.ports.intent import IntentPort
from saga_trpg.ports.narrator import NarrationRequest, NarratorPort
from saga_trpg.ports.tools import ToolExecutionPending
from saga_trpg.rulebooks.commands import (
    CommandError,
    command_help,
    command_verbs,
    parse_command,
    rules_for,
)
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import RulebookSpec, load_rulebook, rulebook_path
from saga_trpg.runtime.hashing import stable_model_hash, stable_value_hash
from saga_trpg.runtime.journal import JournalEntry

logger = logging.getLogger(__name__)
Identity = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HostAction(ClosedModel):
    message_id: Identity
    text: str = Field(min_length=1, max_length=2048)
    context_hidden: bool = False


class RulebookAttachment(ClosedModel):
    """A rulebook carried by an enabled RisuAI module; pins a world when it is created."""

    content: str = Field(min_length=2, max_length=2_000_000)


class HostTurn(ClosedModel):
    schema_version: Literal[1, 2, 3]
    host_instance_id: Identity
    chat_id: Identity
    actor_id: Literal["player"]
    purpose: Literal["action"]
    actions: list[HostAction] = Field(min_length=1, max_length=256)
    rulebook: RulebookAttachment | None = None

    @model_validator(mode="after")
    def distinct_messages(self) -> HostTurn:
        ids = [action.message_id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate host user-message identity")
        if self.actions[-1].context_hidden:
            raise ValueError("the latest user action is hidden; restore its visibility first")
        return self


class ChatMessage(ClosedModel):
    role: Literal["system", "user", "assistant", "developer"]
    content: str = Field(max_length=32000)


class VertexNarrationOptions(ClosedModel):
    schema_version: Literal[1]
    provider: Literal["vertex"]
    context: list[Annotated[str, Field(max_length=4000)]] = Field(
        default_factory=list, max_length=32
    )


class CompletionRequest(ClosedModel):
    model: Literal["saga-srd-demo"]
    messages: list[ChatMessage] = Field(min_length=1, max_length=512)
    stream: Literal[False] = False
    saga: HostTurn
    narration: VertexNarrationOptions | None = None

    @model_validator(mode="after")
    def matching_action(self) -> CompletionRequest:
        latest = self.messages[-1]
        if latest.role != "user" or latest.content != self.saga.actions[-1].text:
            raise ValueError("last prompt message must match the identified user action exactly")
        return self


# The bundled SRD rulebook is the default for new worlds without an attached rulebook, and the
# command grammar for worlds pinned to the hand-written SRD versions (same payload shapes).
DEFAULT_SPEC = load_rulebook(rulebook_path("srd-5.2.1"))


def supported_commands(spec: RulebookSpec) -> str:
    return (
        f"Supported commands for {spec.id}@{spec.version}:\n{command_help(spec)}\n"
        "Values fill the inputs in order; name=value sets one by name; quote text with spaces. "
    )


def canonical_command(*, actor_id: str, text: str, spec: RulebookSpec = DEFAULT_SPEC) -> GameEvent:
    """Pure v2 identity contract; never call an injected/remote interpreter to derive an id."""
    try:
        return parse_command(spec, actor_id=actor_id, text=text)
    except CommandError as exc:
        raise ValueError(
            f"{exc}. {supported_commands(spec)}"
            "Natural language requires protocol 3 and Vertex JSON."
        ) from None


def command_for(
    action: HostAction, actor_id: str, *, natural: bool, spec: RulebookSpec = DEFAULT_SPEC
) -> GameEvent | None:
    """Preserve v2 command identities; raw text is a separate explicit v3 contract."""
    try:
        return canonical_command(actor_id=actor_id, text=action.text, spec=spec)
    except ValueError as exc:
        words = action.text.strip().lower().split()
        if natural and words and words[0] not in command_verbs(spec):
            if action.text.strip().lower() != "*says nothing*":
                return None
        if action.context_hidden:
            raise RepositoryConflict(
                "Hidden user history is not a committed supported action. "
                "Restore it for review; hidden drafts are never executed automatically."
            ) from None
        raise ValueError(
            f"{exc} Empty actions and malformed commands are not interpreted automatically."
        ) from None


class CommandInterpreter:
    """Closed rulebook commands, deliberately independent of generated prose."""

    async def interpret(
        self, *, world_id: str, actor_id: str, text: str, spec: RulebookSpec = DEFAULT_SPEC
    ) -> GameEvent:
        return canonical_command(actor_id=actor_id, text=text, spec=spec)


class RisuBridge:
    def __init__(
        self,
        repository: SQLiteWorldRepository,
        *,
        interpreter: IntentPort | None = None,
        narrator: NarratorPort | None = None,
    ) -> None:
        self.repository = repository
        self.interpreter = interpreter or CommandInterpreter()
        self.narrator = narrator or FallbackNarrator()

    @staticmethod
    def world_id_for(turn: HostTurn, *, version: Literal[1, 2, 3] | None = None) -> str:
        version = version or (3 if turn.schema_version == 3 else 2)
        world_version = 1 if version == 1 else 2
        return "risu-" + stable_value_hash(
            [f"saga-risu-v{world_version}", turn.host_instance_id, turn.chat_id]
        )

    @staticmethod
    def identities(
        turn: HostTurn,
        *,
        version: Literal[1, 2, 3] | None = None,
        spec: RulebookSpec = DEFAULT_SPEC,
    ) -> tuple[str, list[str]]:
        version = version or (3 if turn.schema_version == 3 else 2)
        world_id = RisuBridge.world_id_for(turn, version=version)
        identities = []
        parent = None
        for action in turn.actions:
            content: str | dict = action.text
            action_version = version
            if version != 1:
                event = command_for(action, turn.actor_id, natural=version == 3, spec=spec)
                if event is None:
                    content = {
                        "kind": "natural_language",
                        "schema_version": 1,
                        "actor_id": turn.actor_id,
                        "text": action.text,
                    }
                    action_version = 3
                else:
                    content = {
                        "kind": event.kind,
                        "actor_id": event.actor_id,
                        "payload": event.payload,
                    }
                    action_version = 2
            parent = "risu-" + stable_value_hash(
                [
                    f"saga-risu-action-v{action_version}",
                    world_id,
                    parent,
                    action.message_id,
                    content,
                ]
            )
            identities.append(parent)
        return world_id, identities

    def _spec_for(self, world_id: str, turn: HostTurn) -> RulebookSpec:
        """The rulebook that governs this world's commands.

        An existing world keeps the rulebook it was created with. A new world takes the
        attached rulebook (validated and registered first) or the bundled SRD. Worlds pinned
        to a hand-written SRD version parse commands with the bundled SRD grammar, whose
        payload shapes are identical for the actions those versions have."""
        try:
            selection = self.repository.ruleset_for_world(world_id)
        except RepositoryNotFound:
            if turn.rulebook is None:
                return DEFAULT_SPEC
            manifest, _ = self.repository.register_rulebook(turn.rulebook.content)
            selection = manifest
        rules = self.repository.registry.get(selection.ruleset_id, selection.ruleset_version).rules
        return rules.spec if isinstance(rules, DataRules) else DEFAULT_SPEC

    def _ensure_world(self, world_id: str, spec: RulebookSpec) -> WorldState:
        # New worlds pin the given rulebook; existing worlds keep the version they were
        # created with, so their action set and semantics never change under them.
        definition = self.repository.registry.get(spec.id, spec.version)
        self.repository.create_world(
            definition.example_world(world_id),
            if_missing=True,
            ruleset_id=spec.id,
            ruleset_version=spec.version,
        )
        return self.repository.get_initial_state(world_id)

    async def _natural_intent(
        self,
        world_id: str,
        identities: list[str],
        turn: HostTurn,
        parent: JournalEntry | None,
        vertex: VertexNarrator,
    ) -> GameEvent:
        initial = self._ensure_world(world_id, DEFAULT_SPEC)
        # A branch head can move while inference runs. Only the verified committed prefix
        # (or immutable ruleset genesis) supplies the snapshot bound to this interpretation.
        state = parent.result.state if parent else initial
        call = ToolCall(
            world_id=world_id,
            branch_id="main",
            event_id=identities[-1],
            tool_call_key="risu-natural-intent-v1",
            tool_name="vertex-intent",
            tool_version="1",
            protocol_version="saga-risu-intent-v1",
            arguments={
                "text": turn.actions[-1].text,
                "actor_id": turn.actor_id,
                "targets": {
                    key: aliases for key, aliases in TARGET_ALIASES.items() if key in state.actors
                },
            },
            context_hash=stable_model_hash(state),
        )
        try:
            execution = await asyncio.wait_for(
                asyncio.to_thread(
                    self.repository.tool_tape.execute, call, VertexIntentTool(vertex)
                ),
                timeout=27,
            )
        except (TimeoutError, ToolExecutionPending):
            # Cancellation does not revoke a durable claim or allow another remote attempt.
            raise ToolExecutionPending(PENDING) from None
        return event_from_result(execution.result, call.arguments)

    def _read_prefix(self, world_id: str, identities: list[str]) -> tuple[str, JournalEntry] | None:
        """The committed latest action and its branch, verified against the whole prefix."""
        if not identities:
            return None
        try:
            found = self.repository.locate_event(world_id, identities[-1])
        except RepositoryNotFound:
            return None
        if not found:
            return None
        # Report the branch that committed the action rather than a fork that inherited it.
        for item in sorted(found, key=lambda item: (item.seq <= item.fork_seq, item.branch_id)):
            entries = self._matching(world_id, item, identities)
            if entries is not None:
                return item.branch_id, entries[-1]
        raise RepositoryConflict("SAGA branch history conflicts with host action identities")

    def _matching(
        self, world_id: str, item: EventLocation, identities: list[str]
    ) -> list[JournalEntry] | None:
        """The branch prefix through a located event, only if it is exactly these actions."""
        if item.seq != len(identities):
            return None
        entries = self.repository.entries(world_id, branch_id=item.branch_id, through_seq=item.seq)
        if [entry.committed.event.event_id for entry in entries] != identities:
            return None
        return entries

    def _branch_for(self, world_id: str, identities: list[str]) -> tuple[str, bool]:
        """Continue the branch whose head is the parent; fork only when history diverges.

        Returns the branch and whether this call created it.

        Explicit event ids are branch scoped, so a branch merely containing the parent id is
        not trusted: the branch written to, or forked from, must hold the exact parent prefix.
        """
        event_id, prefix = identities[-1], identities[:-1]
        found = self.repository.locate_event(world_id, prefix[-1] if prefix else None)
        heads = [
            item
            for item in found
            if item.seq == item.head_seq
            and item.pending_event_id in (None, event_id)
            # An empty fork is reserved for the action it is named after.
            and (
                item.branch_id in ("main", event_id)
                or item.head_seq > item.fork_seq
                or item.pending_event_id == event_id
            )
        ]
        heads.sort(key=lambda item: (item.pending_event_id != event_id, item.branch_id))
        for item in heads:
            if self._matching(world_id, item, prefix) is not None:
                return item.branch_id, False
        for item in found:
            if self._matching(world_id, item, prefix) is not None:
                self.repository.fork(
                    world_id,
                    branch_id=event_id,
                    source_branch_id=item.branch_id,
                    at_event_id=prefix[-1] if prefix else None,
                    # A racing identical request may have committed since this request read.
                    unless_committed=event_id,
                )
                return event_id, True
        raise RepositoryConflict("SAGA branch history conflicts with host action identities")

    async def complete(
        self, request: CompletionRequest, *, vertex_credentials: SecretStr | None = None
    ) -> dict:
        if (request.narration is None) != (vertex_credentials is None):
            raise ValueError(
                "Vertex narration requires its configuration and service-account JSON."
            )
        narrator = (
            VertexNarrator(vertex_credentials) if vertex_credentials is not None else self.narrator
        )
        turn = request.saga
        replayed = True
        spec = DEFAULT_SPEC
        legacy_world, legacy_ids = self.identities(turn, version=1)
        try:
            self.repository.ruleset_for_world(legacy_world)
            legacy_exists = True
        except RepositoryNotFound:
            legacy_exists = False
        identity_version = 1 if legacy_exists else 2
        if legacy_exists:
            world_id, identities = legacy_world, legacy_ids
            saved = self._read_prefix(world_id, identities)
            if saved is None:
                raise RepositoryConflict(
                    "This chat has read-only v1 history. Only its exact original text and "
                    "message-id prefix can replay. Restore that history, or start a new host "
                    "chat for v2 commands. Legacy cosmetic edits cannot be reconstructed safely."
                )
        else:
            if turn.schema_version == 1:
                raise RepositoryConflict(
                    "Bridge v1 is read-only. Update the plugin to protocol v2 for new chats."
                )
            spec = self._spec_for(self.world_id_for(turn), turn)
            world_id, identities = self.identities(turn, spec=spec)
            if turn.schema_version == 3 and any(
                command_for(action, turn.actor_id, natural=True, spec=spec) is None
                for action in turn.actions
            ):
                identity_version = 3
            for index, action in enumerate(turn.actions):
                if (
                    action.context_hidden
                    and self._read_prefix(world_id, identities[: index + 1]) is None
                ):
                    raise RepositoryConflict(
                        "Hidden history has no matching committed prefix. Restore the first "
                        "hidden/edited turn for review, or remove a never-executed draft. "
                        "No hidden action was executed and no campaign was reset."
                    )
            saved = self._read_prefix(world_id, identities)
        event_id = identities[-1]
        if saved is None:
            parent = self._read_prefix(world_id, identities[:-1]) if len(identities) > 1 else None
            if len(identities) > 1 and parent is None:
                raise RepositoryConflict(
                    "Uncommitted or edited earlier history. Rewind the host chat to the first "
                    "changed user turn and generate there before adding later actions."
                )
            expected = command_for(
                turn.actions[-1], turn.actor_id, natural=turn.schema_version == 3, spec=spec
            )
            if expected is None:
                if spec.id != DEFAULT_SPEC.id:
                    raise ValueError(
                        "Natural-language actions are only interpreted for the SRD demo "
                        "rulebook; use this rulebook's commands."
                    )
                if vertex_credentials is None:
                    raise ValueError(
                        "자연어 행동에는 Vertex 서비스 계정 JSON이 필요합니다. "
                        "명령은 키 없이 사용할 수 있습니다."
                    )
                proposed = await self._natural_intent(
                    world_id,
                    identities,
                    turn,
                    parent[1] if parent else None,
                    narrator,
                )
            else:
                arguments = {
                    "world_id": world_id,
                    "actor_id": turn.actor_id,
                    "text": turn.actions[-1].text,
                }
                if isinstance(self.interpreter, CommandInterpreter):
                    arguments["spec"] = spec
                proposed = await self.interpreter.interpret(**arguments)
                proposed = GameEvent.model_validate(proposed.model_dump())
            # Natural-language proposals stay limited to SRD roll actions; commands cover the
            # whole rulebook.
            allowed = rules_for(spec).payload_models if expected is not None else ROLL_ACTIONS
            payload_model = allowed.get(proposed.kind)
            if payload_model is None or proposed.actor_id != turn.actor_id:
                raise ValueError("intent must select a supported action for the identified actor")
            payload = payload_model.model_validate(proposed.payload)
            if expected is not None and (
                proposed.kind != expected.kind or payload.model_dump() != expected.payload
            ):
                raise ValueError("interpreter proposal differs from the canonical v2 command")
            event = GameEvent(
                event_id=event_id,
                idempotency_key=event_id,
                parent_event_id=identities[-2] if len(identities) > 1 else None,
                actor_id=turn.actor_id,
                kind=proposed.kind,
                payload=payload.model_dump(),
            )
            self._ensure_world(world_id, spec)
            # Racing requests may move a head, create the fork or commit this action first.
            # Every commit is guarded in its own transaction, so reselect instead of guessing.
            for attempt in range(3):
                saved = self._read_prefix(world_id, identities)
                if saved is not None:
                    break
                forked = False
                try:
                    branch_id, forked = self._branch_for(world_id, identities)
                    outcome = self.repository.dispatch(
                        world_id,
                        event,
                        branch_id=branch_id,
                        expected_seq=len(identities) - 1,
                        unique_in_world=True,
                    )
                except (RepositoryConflict, RepositoryNotFound):
                    # Not found here means a racing request discarded the empty fork this
                    # one had selected; reselecting recreates it. The world itself exists.
                    if attempt == 2:
                        raise
                    continue
                except ValueError:
                    # The rules rejected the edited action before any commit; do not leave
                    # the fork made for it behind. A racing identical request that already
                    # used the branch keeps it, because discard refuses committed branches.
                    if forked:
                        with contextlib.suppress(RepositoryConflict, RepositoryNotFound):
                            self.repository.discard_empty_branch(world_id, branch_id)
                    raise
                saved, replayed = (branch_id, outcome.entry), outcome.replayed
                break
        branch_id, saved = saved

        narration = NarrationRequest(
            world_id=world_id,
            event=saved.committed.event.model_copy(deep=True),
            result=saved.result.model_copy(deep=True),
            recent_dialogue=request.narration.context if request.narration else [],
        )
        try:
            prose = await narrator.render(narration)
            if not isinstance(prose, str) or not prose.strip():
                raise ValueError("empty narrator response")
        except Exception:
            logger.warning("Risu narrator failed after commit; using recorded facts")
            prose = await FallbackNarrator().render(
                NarrationRequest(
                    world_id=world_id,
                    event=saved.committed.event.model_copy(deep=True),
                    result=saved.result.model_copy(deep=True),
                )
            )
            if request.narration:
                prose += "\n\n모델 서술에 실패하여 확정된 기본 서술을 표시합니다."
        result = saved.result.model_dump(mode="json")
        return {
            "id": "chatcmpl-" + event_id,
            "object": "chat.completion",
            "created": 0,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": prose},
                    "finish_reason": "stop",
                }
            ],
            "saga": {
                "schema_version": turn.schema_version,
                "identity_version": identity_version,
                "legacy_read_only": legacy_exists,
                "world_id": world_id,
                "branch_id": branch_id,
                "event_id": event_id,
                "seq": saved.committed.seq,
                "replayed": replayed,
                **result,
            },
        }


def router(bridge: RisuBridge) -> APIRouter:
    routes = APIRouter()

    @routes.post("/v1/chat/completions")
    async def complete(
        request: CompletionRequest,
        vertex_secret: Annotated[SecretStr | None, Header(alias="x-saga-vertex-secret")] = None,
    ) -> dict:
        credentials = None
        if vertex_secret is not None:
            try:
                encoded = vertex_secret.get_secret_value()
                if not 1 <= len(encoded) <= 24000:
                    raise ValueError
                credentials = SecretStr(base64.b64decode(encoded, validate=True).decode("utf-8"))
            except Exception:
                raise ValueError("Vertex 서비스 계정 JSON을 확인하세요.") from None
        return await bridge.complete(request, vertex_credentials=credentials)

    @routes.get("/v1/models")
    async def models() -> dict:
        return {
            "object": "list",
            "data": [{"id": "saga-srd-demo", "object": "model", "created": 0, "owned_by": "saga"}],
        }

    return routes
