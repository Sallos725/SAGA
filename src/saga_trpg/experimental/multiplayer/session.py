"""Pure admission decorator executed by the existing transactional RuntimeEngine."""

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import RuleResult, WorldState
from saga_trpg.experimental.multiplayer.models import (
    ActionProposal,
    CampaignSession,
    Participant,
    TurnState,
)
from saga_trpg.modules.base import RuleModule
from saga_trpg.ports.dice import DicePort
from saga_trpg.runtime.hashing import stable_value_hash


class AdmissionError(ValueError):
    """A fixed public code, never raw rule errors or hidden identifiers."""


def participant_for(session: CampaignSession, participant_id: str) -> Participant:
    for participant in session.participants:
        if participant.participant_id == participant_id:
            return participant
    raise AdmissionError("forbidden")


def turn_at(session: CampaignSession, seq: int) -> TurnState:
    return TurnState(
        mode=session.mode,
        round=seq // len(session.turn_order) + 1,
        active_actor_id=(
            session.turn_order[seq % len(session.turn_order)]
            if session.mode == "turn_based"
            else None
        ),
        turn_order=session.turn_order,
        turn_revision=seq,
    )


def action_event(proposal: ActionProposal) -> GameEvent:
    identity = "mp:" + stable_value_hash([proposal.participant_id, proposal.client_action_id])
    return GameEvent(
        event_id=identity,
        idempotency_key=identity,
        kind="experiment.multiplayer.action",
        actor_id=proposal.actor_id,
        payload=proposal.model_dump(mode="json"),
    )


class SessionRules:
    """No separate scheduler/state store: one accepted commit advances one turn.

    The immutable manifest is supplied by the trusted host and pinned in the stored
    ruleset version. Reopening with a different roster/policy fails closed. Branch id
    is excluded so normal forks retain their inherited identities and semantics.
    """

    def __init__(self, session: CampaignSession, rules: RuleModule) -> None:
        self._session = CampaignSession.model_validate_json(session.model_dump_json())
        self._rules = rules
        self.name = f"experiment.multiplayer/{rules.name}"
        fingerprint = stable_value_hash(session.model_dump(mode="json", exclude={"branch_id"}))
        self.version = f"1/{rules.version}/{fingerprint}"

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult:
        if (
            event.kind != "experiment.multiplayer.action"
            or state.world_id != self._session.world_id
        ):
            raise AdmissionError("forbidden")
        try:
            proposal = ActionProposal.model_validate(event.payload)
        except ValueError:
            raise AdmissionError("invalid_action") from None
        participant = participant_for(self._session, proposal.participant_id)
        expected = action_event(proposal)
        if (
            participant.participant_type not in ("human", "ai_agent")
            or "propose" not in participant.permissions
            or participant.actor_id != proposal.actor_id
            or event != expected
        ):
            raise AdmissionError("forbidden")
        if proposal.expected_parent_seq != state.seq or proposal.turn_revision != state.seq:
            raise AdmissionError("stale")
        turn = turn_at(self._session, state.seq)
        if turn.mode == "turn_based" and turn.active_actor_id != proposal.actor_id:
            raise AdmissionError("not_active")
        action_spec = next(
            (action for action in self._session.actions if action.kind == proposal.action.kind),
            None,
        )
        if action_spec is None:
            raise AdmissionError("invalid_action")
        actor = state.actors.get(proposal.actor_id)
        if actor is None or actor.actor_id != proposal.actor_id:
            raise AdmissionError("forbidden")
        # The host classifies actor references, including required fields. Missing/null
        # references must not reach permissive legacy rules that synthesize actors.
        reference_fields = set(action_spec.actor_reference_fields)
        if "target_id" in proposal.action.payload:
            reference_fields.add("target_id")
        for field in sorted(reference_fields):
            target = proposal.action.payload.get(field)
            if not isinstance(target, str) or target not in participant.visible_actor_ids:
                raise AdmissionError("target_unavailable")
            target_actor = state.actors.get(target)
            if target_actor is None or target_actor.actor_id != target:
                raise AdmissionError("target_unavailable")
        inner = GameEvent(
            event_id=event.event_id,
            idempotency_key=event.idempotency_key,
            kind=proposal.action.kind,
            actor_id=proposal.actor_id,
            payload=proposal.action.payload,
        )
        result = self._rules.apply(state=state, event=inner, dice=dice)
        result.trace.insert(0, f"multiplayer-v1: admitted turn {state.seq}")
        return result
