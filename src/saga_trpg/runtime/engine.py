from __future__ import annotations

from dataclasses import dataclass

from saga_trpg.domain.events import (
    NARRATIVE_TURN_KIND,
    CommittedEvent,
    GameEvent,
    NarrativeTurnPayload,
)
from saga_trpg.domain.models import RuleResult, WorldState
from saga_trpg.modules.base import RuleModule
from saga_trpg.ports.dice import DicePort
from saga_trpg.runtime.hashing import stable_model_hash
from saga_trpg.runtime.journal import JournalEntry, JournalPort
from saga_trpg.runtime.replay import replay_recorded_results


class IdempotencyConflict(ValueError):
    """A stable identity was reused for a different event proposal."""


def validate_event_retry(proposed: GameEvent, saved: GameEvent) -> None:
    identity = {"event_id": None, "idempotency_key": None}
    same_action = stable_model_hash(proposed.model_copy(update=identity)) == stable_model_hash(
        saved.model_copy(update=identity)
    )
    same_id = proposed.event_id is None or proposed.event_id == saved.event_id
    same_key = proposed.idempotency_key is None or proposed.idempotency_key == saved.idempotency_key
    if not (same_action and same_id and same_key):
        raise IdempotencyConflict("idempotency conflict: identity reused for another action")


@dataclass
class DispatchOutcome:
    entry: JournalEntry
    replayed: bool = False


class RuntimeEngine:
    def __init__(
        self,
        *,
        initial_state: WorldState,
        rules: RuleModule,
        dice: DicePort,
        journal: JournalPort,
        event_id_prefix: str | None = None,
    ) -> None:
        self.state = initial_state.model_copy(deep=True)
        self.rules = rules
        self.dice = dice
        self.journal = journal
        self.event_id_prefix = event_id_prefix or initial_state.world_id

    def dispatch(self, proposed: GameEvent) -> DispatchOutcome:
        event = GameEvent.model_validate(proposed.model_dump())
        by_id = self.journal.find_by_event_id(event.event_id) if event.event_id else None
        by_key = (
            self.journal.find_by_idempotency_key(event.idempotency_key)
            if event.idempotency_key
            else None
        )
        if by_id is not None and by_key is not None and by_id != by_key:
            raise IdempotencyConflict("idempotency conflict: identities refer to different events")
        existing = by_id or by_key
        if existing is not None:
            validate_event_retry(event, existing.committed.event)
            return DispatchOutcome(entry=existing, replayed=True)

        next_seq = self.state.seq + 1
        event.event_id = event.event_id or f"{self.event_id_prefix}:e{next_seq}"

        existing = self.journal.find_by_event_id(event.event_id)
        if existing:
            raise IdempotencyConflict("idempotency conflict: generated event id already exists")

        before_hash = stable_model_hash(self.state)
        working = self.state.model_copy(deep=True)
        if event.kind == NARRATIVE_TURN_KIND:
            NarrativeTurnPayload.model_validate(event.payload)
            if event.actor_id not in working.actors:
                raise ValueError(f"unknown actor: {event.actor_id}")
            result = RuleResult(
                state=working,
                facts=[f"{event.actor_id} takes a narrative turn"],
                rolls=[],
                patches=[],
                trace=[f"runtime:{NARRATIVE_TURN_KIND} actor={event.actor_id}"],
                resolution=None,
            )
        else:
            result = self.rules.apply(state=working, event=event, dice=self.dice)
        result.state.seq = next_seq
        after_hash = stable_model_hash(result.state)

        committed = CommittedEvent(
            event=event,
            seq=next_seq,
            state_hash_before=before_hash,
            state_hash_after=after_hash,
        )
        entry = JournalEntry(committed=committed, result=result)
        replay_recorded_results(self.state, [entry])

        # Journal before exposing the new in-memory state. Persistent JournalPort implementations
        # should fail atomically if they cannot record the event.
        self.journal.append(entry)
        self.state = result.state.model_copy(deep=True)
        return DispatchOutcome(entry=entry, replayed=False)

    def get_event(self, event_id: str) -> JournalEntry | None:
        return self.journal.find_by_event_id(event_id)
