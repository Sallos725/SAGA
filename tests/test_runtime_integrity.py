import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_runtime import make_engine

from nmos_trpg.domain.events import GameEvent
from nmos_trpg.runtime.journal import JsonlJournal
from nmos_trpg.runtime.replay import replay_recorded_results


def flag_event(**kwargs) -> GameEvent:
    return GameEvent(
        kind="set_flag", actor_id="player", payload={"key": "gate", "value": True}, **kwargs
    )


def test_idempotency_key_reuses_event_without_rewinding_state() -> None:
    engine = make_engine()
    first = engine.dispatch(flag_event(idempotency_key="request-1"))
    engine.dispatch(GameEvent(kind="time_advance", actor_id="player"))
    latest = engine.state.model_copy(deep=True)

    repeated = engine.dispatch(flag_event(idempotency_key="request-1"))

    assert repeated.replayed
    assert repeated.entry == first.entry
    assert engine.state == latest
    assert len(engine.journal.entries()) == 2


@pytest.mark.parametrize("identity", [{"event_id": "e1"}, {"idempotency_key": "request-1"}])
def test_reusing_identity_for_different_action_is_rejected(identity) -> None:
    engine = make_engine()
    engine.dispatch(flag_event(**identity))
    before = engine.state.model_copy(deep=True)
    with pytest.raises(ValueError, match="conflict"):
        engine.dispatch(GameEvent(kind="time_advance", actor_id="player", **identity))
    assert engine.state == before
    assert len(engine.journal.entries()) == 1


@pytest.mark.parametrize("corruption", ["before", "after", "seq", "world", "trace", "order"])
def test_replay_rejects_corruption(corruption: str) -> None:
    engine = make_engine()
    initial = engine.state.model_copy(deep=True)
    engine.dispatch(flag_event())
    engine.dispatch(GameEvent(kind="time_advance", actor_id="player"))
    entries = engine.journal.entries()
    if corruption == "before":
        entries[0].committed.state_hash_before = "wrong"
    elif corruption == "after":
        entries[0].result.state.flags["gate"] = False
    elif corruption == "seq":
        entries[0].committed.seq = 42
    elif corruption == "world":
        entries[0].result.state.world_id = "another-world"
    elif corruption == "trace":
        entries[0].result.trace = []
    else:
        entries.reverse()
    with pytest.raises(ValueError):
        replay_recorded_results(initial, entries)


def test_condition_hash_is_stable_across_processes() -> None:
    code = (
        "from nmos_trpg.domain.models import WorldState, ActorState; "
        "from nmos_trpg.runtime.hashing import stable_model_hash; "
        "print(stable_model_hash(WorldState(world_id='w', actors={'p': "
        "ActorState(actor_id='p', conditions={'poisoned','stunned','hidden','blessed'})})))"
    )
    hashes = {
        subprocess.check_output(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            text=True,
        ).strip()
        for seed in range(4)
    }
    assert len(hashes) == 1


def test_failed_jsonl_write_does_not_publish_cached_event(tmp_path, monkeypatch) -> None:
    engine = make_engine()
    engine.journal = JsonlJournal(tmp_path / "journal.jsonl")
    before = engine.state.model_copy(deep=True)

    def fail_open(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(OSError, match="disk unavailable"):
        engine.dispatch(flag_event())
    assert engine.state == before
    assert engine.journal.entries() == []


def test_idempotency_does_not_equate_boolean_and_numeric_values() -> None:
    engine = make_engine()
    engine.dispatch(flag_event(idempotency_key="flag"))
    numeric = flag_event(idempotency_key="flag")
    numeric.payload["value"] = 1
    with pytest.raises(ValueError, match="conflict"):
        engine.dispatch(numeric)


def test_rules_cannot_commit_an_unsupported_state_version() -> None:
    engine = make_engine()
    original_apply = engine.rules.apply

    def invalid_version(**kwargs):
        result = original_apply(**kwargs)
        result.state.schema_version = 999
        return result

    engine.rules.apply = invalid_version
    with pytest.raises(ValueError):
        engine.dispatch(flag_event())
    assert engine.state.seq == 0
    assert engine.journal.entries() == []
