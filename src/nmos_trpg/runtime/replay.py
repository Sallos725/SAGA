from __future__ import annotations

from nmos_trpg.domain.models import WorldState
from nmos_trpg.runtime.hashing import stable_model_hash
from nmos_trpg.runtime.journal import JournalEntry


class ReplayError(ValueError):
    """The recorded transition chain is not a valid replay of the supplied state."""


def replay_recorded_results(initial_state: WorldState, entries: list[JournalEntry]) -> WorldState:
    """Rebuild state from already-recorded transition results without re-running tools.

    This reference scaffold stores full post-event state in each entry. A production implementation
    may prefer snapshots + event/result records, but replay must never re-roll journaled dice merely
    to reconstruct a committed branch.
    """
    try:
        state = WorldState.model_validate(initial_state.model_dump())
    except ValueError as exc:
        raise ReplayError("invalid initial state schema") from exc
    event_ids: set[str] = set()
    idempotency_keys: set[str] = set()
    for entry in entries:
        try:
            entry = JournalEntry.model_validate(entry.model_dump())
        except ValueError as exc:
            raise ReplayError("invalid journal schema or version") from exc
        committed = entry.committed
        event_id = committed.event.event_id
        key = committed.event.idempotency_key
        if not event_id or event_id in event_ids:
            raise ReplayError("missing or duplicate committed event id")
        if key is not None and key in idempotency_keys:
            raise ReplayError("duplicate committed idempotency key")
        if committed.seq != state.seq + 1 or entry.result.state.seq != committed.seq:
            raise ReplayError("journal sequence is not contiguous")
        if entry.result.state.world_id != state.world_id:
            raise ReplayError("journal world does not match initial state")
        if committed.state_hash_before != stable_model_hash(state):
            raise ReplayError("journal before-state hash mismatch")
        if committed.state_hash_after != stable_model_hash(entry.result.state):
            raise ReplayError("journal after-state hash mismatch")
        if not entry.result.trace:
            raise ReplayError("committed event requires a trace")
        event_ids.add(event_id)
        if key is not None:
            idempotency_keys.add(key)
        state = entry.result.state.model_copy(deep=True)
    return state
