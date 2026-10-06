"""Deterministic hidden validators evaluated against the final state and the journal."""

from __future__ import annotations

import operator
from collections.abc import Sequence
from typing import Any

from saga_trpg.benchmark.models import (
    ActorValidator,
    EventCountValidator,
    EventMatch,
    EventOrderValidator,
    IllegalAttemptsValidator,
    RecordValidator,
    StateValidator,
    Validator,
)
from saga_trpg.domain.models import WorldState
from saga_trpg.runtime.journal import JournalEntry

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def _matches(match: EventMatch, entry: JournalEntry) -> bool:
    event = entry.committed.event
    if match.kind is not None and event.kind != match.kind:
        return False
    if match.actor_id is not None and event.actor_id != match.actor_id:
        return False
    return all(event.payload.get(key) == value for key, value in match.payload.items())


_MISSING = object()


def _path(state: WorldState, path: str) -> Any:
    current: Any = state.model_dump(mode="json")
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def evaluate_validators(
    validators: Sequence[Validator],
    *,
    state: WorldState,
    entries: Sequence[JournalEntry],
    illegal_attempts: int,
) -> dict[str, bool]:
    verdicts: dict[str, bool] = {}
    for item in validators:
        if isinstance(item, ActorValidator):
            actor = state.actors.get(item.actor)
            if actor is None:
                verdicts[item.id] = False
                continue
            value = (
                getattr(actor, item.field)
                if item.field in ("hp", "max_hp")
                else actor.stats.get(item.field)
            )
            try:
                verdicts[item.id] = value is not None and _OPS[item.op](value, item.value)
            except TypeError:
                verdicts[item.id] = False
        elif isinstance(item, StateValidator):
            found = _path(state, item.path)
            verdicts[item.id] = found is not _MISSING and found == item.equals
        elif isinstance(item, EventCountValidator):
            count = sum(1 for entry in entries if _matches(item.match, entry))
            verdicts[item.id] = count >= item.min and (item.max is None or count <= item.max)
        elif isinstance(item, EventOrderValidator):
            first_before = next(
                (i for i, entry in enumerate(entries) if _matches(item.before, entry)), None
            )
            first_after = next(
                (i for i, entry in enumerate(entries) if _matches(item.after, entry)), None
            )
            verdicts[item.id] = (
                first_before is not None and first_after is not None and first_before < first_after
            )
        elif isinstance(item, IllegalAttemptsValidator):
            verdicts[item.id] = illegal_attempts <= item.max
        elif isinstance(item, RecordValidator):
            stored = state.flags.get(item.collection, {})
            verdicts[item.id] = isinstance(stored, dict) and any(
                isinstance(record, dict) and record.get(item.field) == item.equals
                for record in stored.values()
            )
    return verdicts
