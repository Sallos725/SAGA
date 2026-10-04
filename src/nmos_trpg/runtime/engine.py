from __future__ import annotations

from dataclasses import dataclass

from nmos_trpg.domain.events import CommittedEvent, GameEvent
from nmos_trpg.domain.models import RuleResult, WorldState
from nmos_trpg.modules.base import RuleModule
from nmos_trpg.ports.dice import DicePort
from nmos_trpg.runtime.hashing import stable_model_hash
from nmos_trpg.runtime.journal import JournalEntry, JournalPort
from nmos_trpg.runtime.replay import replay_recorded_results


class IdempotencyConflict(ValueError):
    """A stable identity was reused for a different event proposal."""


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
            saved = existing.committed.event
            identity = {"event_id": None, "idempotency_key": None}
            # Python equality treats True and 1 as equal; persisted JSON actions must not.
            same_action = stable_model_hash(event.model_copy(update=identity)) == stable_model_hash(
                saved.model_copy(update=identity)
            )
            same_id = event.event_id is None or event.event_id == saved.event_id
            same_key = (
                event.idempotency_key is None or event.idempotency_key == saved.idempotency_key
            )
            if not (same_action and same_id and same_key):
                raise IdempotencyConflict(
                    "idempotency conflict: identity reused for another action"
                )
            return DispatchOutcome(entry=existing, replayed=True)

        next_seq = self.state.seq + 1
        event.event_id = event.event_id or f"{self.event_id_prefix}:e{next_seq}"

        existing = self.journal.find_by_event_id(event.event_id)
        if existing:
            raise IdempotencyConflict("idempotency conflict: generated event id already exists")

        before_hash = stable_model_hash(self.state)
        working = self.state.model_copy(deep=True)
        result: RuleResult = self.rules.apply(state=working, event=event, dice=self.dice)
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
