import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_repository import attack, initial_state

from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.adapters.storage.tool_tape import SQLiteToolResultTape
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.tools import ToolCall
from saga_trpg.ports.tools import ToolBinding, ToolExecutionPending, ToolIdentityConflict
from saga_trpg.runtime.rng import DeterministicDice


class FakeTool:
    def __init__(self):
        self.calls = []

    def execute(self, call, *, idempotency_key):
        self.calls.append((call, idempotency_key))
        if call.tool_name == "dice":
            return (
                DeterministicDice("external")
                .roll(
                    world_id=call.world_id,
                    event_id=call.event_id,
                    roll_key=call.arguments["roll_key"],
                    expression=call.arguments["expression"],
                )
                .model_dump(mode="json")
            )
        return {"encounter": "owlbear", "value": 7}


def binding(tool):
    return ToolBinding(name="dice", version="1", protocol_version="fake-1", executor=tool)


@pytest.fixture
def tape(tmp_path):
    path = tmp_path / "worlds.sqlite3"
    repo = SQLiteWorldRepository(path)
    repo.create_world(initial_state())
    return SQLiteToolResultTape(path)


def call(**updates):
    return ToolCall(
        world_id="test",
        branch_id="main",
        event_id="event-1",
        tool_call_key="lookup",
        tool_name="encounter",
        tool_version="1",
        protocol_version="fake-1",
        arguments={"table": "forest", "options": {"b": 2, "a": 1}},
    ).model_copy(update=updates)


def test_generic_result_survives_restart_and_argument_order(tape):
    tool = FakeTool()
    first = tape.execute(call(), tool)
    reordered = call(arguments={"options": {"a": 1, "b": 2}, "table": "forest"})
    restarted = SQLiteToolResultTape(tape.path)
    assert restarted.execute(reordered, tool) == first
    assert restarted.get(call()) == first
    assert first.status == "completed"
    assert len(tool.calls) == 1
    assert tool.calls[0][1].startswith("saga-tool-")


@pytest.mark.parametrize(
    "updates",
    [
        {"arguments": {"table": "desert"}},
        {"tool_name": "different"},
        {"tool_version": "2"},
        {"protocol_version": "new"},
        {"context_hash": "changed"},
    ],
)
def test_same_identity_rejects_different_request(tape, updates):
    tool = FakeTool()
    tape.execute(call(), tool)
    with pytest.raises(ToolIdentityConflict):
        tape.execute(call(**updates), tool)
    assert len(tool.calls) == 1


def test_boolean_and_number_are_not_the_same_arguments(tape):
    tape.execute(call(arguments={"value": True}), FakeTool())
    with pytest.raises(ToolIdentityConflict):
        tape.execute(call(arguments={"value": 1}), FakeTool())


def test_inflight_call_has_one_durable_owner(tape):
    entered, release = Event(), Event()

    class SlowTool(FakeTool):
        def execute(self, call, *, idempotency_key):
            entered.set()
            assert release.wait(5)
            return super().execute(call, idempotency_key=idempotency_key)

    tool = SlowTool()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(tape.execute, call(), tool)
        assert entered.wait(5)
        try:
            with pytest.raises(ToolExecutionPending):
                SQLiteToolResultTape(tape.path).execute(call(), tool)
        finally:
            release.set()
        assert future.result().status == "completed"
    assert len(tool.calls) == 1


def test_tool_exception_is_ambiguous_and_never_automatically_retried(tape):
    class FailedTool(FakeTool):
        def execute(self, call, *, idempotency_key):
            raise TimeoutError("remote response lost")

    with pytest.raises(TimeoutError):
        tape.execute(call(), FailedTool())
    tool = FakeTool()
    with pytest.raises(ToolExecutionPending):
        SQLiteToolResultTape(tape.path).execute(call(), tool)
    assert tool.calls == []
    assert tape.get(call()).status == "pending"


def test_event_commit_failure_reuses_durable_rolls(tape):
    tool = FakeTool()
    repo = SQLiteWorldRepository(tape.path, external_dice=binding(tool), snapshot_interval=1)
    with sqlite3.connect(tape.path) as connection:
        connection.execute("""CREATE TRIGGER fail_snapshot BEFORE INSERT ON snapshots
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.DatabaseError, match="injected failure"):
        repo.dispatch("test", attack(event_id="turn"))
    assert repo.get_state("test").seq == 0
    assert repo.entries("test") == []
    assert len(tool.calls) == 2
    with sqlite3.connect(tape.path) as connection:
        assert connection.execute("SELECT status FROM tool_executions").fetchall() == [
            ("completed",),
            ("completed",),
        ]
        connection.execute("DROP TRIGGER fail_snapshot")
    restarted = SQLiteWorldRepository(tape.path, external_dice=binding(tool))
    result = restarted.dispatch("test", attack(event_id="turn"))
    assert result.entry.result.state.seq == 1
    assert restarted.replay("test") == result.entry.result.state
    assert len(tool.calls) == 2
    with sqlite3.connect(tape.path) as connection:
        assert connection.execute("SELECT status FROM tool_executions").fetchall() == [
            ("committed",),
            ("committed",),
        ]


def test_world_and_branch_scopes_are_independent(tape):
    repo = SQLiteWorldRepository(tape.path)
    repo.create_world(initial_state("other"))
    repo.fork("test", branch_id="alternate", at_event_id=None)
    tool = FakeTool()
    for request in (call(), call(world_id="other"), call(branch_id="alternate")):
        tape.execute(request, tool)
        tape.execute(request, tool)
    assert len(tool.calls) == 3
    assert len({key for _, key in tool.calls}) == 3


def test_failed_result_write_preserves_ambiguous_claim(tape):
    with sqlite3.connect(tape.path) as connection:
        connection.execute("""CREATE TRIGGER fail_tool_result BEFORE UPDATE ON tool_executions
            BEGIN SELECT RAISE(ABORT, 'injected tape failure'); END""")
    tool = FakeTool()
    with pytest.raises(sqlite3.DatabaseError, match="injected tape failure"):
        tape.execute(call(), tool)
    assert tape.get(call()).status == "pending"
    with pytest.raises(ToolExecutionPending):
        SQLiteToolResultTape(tape.path).execute(call(), tool)
    assert len(tool.calls) == 1


@pytest.mark.parametrize("payload", [float("nan"), float("inf")])
def test_nonfinite_arguments_fail_before_execution(tape, payload):
    tool = FakeTool()
    with pytest.raises(ValueError):
        tape.execute(call(arguments={"value": payload}), tool)
    assert tool.calls == []
    assert tape.get(call(arguments={"value": payload})) is None


def test_pending_event_rejects_changed_action_and_local_bypass(tape):
    class FailedTool(FakeTool):
        def execute(self, call, *, idempotency_key):
            raise TimeoutError("lost result")

    repo = SQLiteWorldRepository(tape.path, external_dice=binding(FailedTool()))
    with pytest.raises(TimeoutError):
        repo.dispatch("test", attack(event_id="turn", idempotency_key="key"))
    changed = attack(event_id="turn", idempotency_key="key")
    changed.payload["target_id"] = "another-target"  # identical dice arguments, different action
    with pytest.raises(ValueError, match="conflict"):
        repo.dispatch("test", changed)
    local = SQLiteWorldRepository(tape.path)
    with pytest.raises(ToolExecutionPending):
        local.dispatch("test", attack(idempotency_key="key"))
    with pytest.raises(ValueError, match="unresolved"):
        local.dispatch("test", GameEvent(event_id="next", kind="time_advance", actor_id="player"))
    assert local.get_state("test") == initial_state()
    assert local.entries("test") == []
    repo.fork("test", branch_id="independent", at_event_id=None)
    assert local.dispatch("test", attack(event_id="turn"), branch_id="independent")


@pytest.mark.asyncio
async def test_external_rerender_replay_fork_and_restart_never_execute(tape):
    from saga_trpg.adapters.narrator.fallback import FallbackNarrator
    from saga_trpg.ports.narrator import NarrationRequest

    tool = FakeTool()
    repo = SQLiteWorldRepository(tape.path, external_dice=binding(tool))
    first = repo.dispatch("test", attack(idempotency_key="key"))
    assert len(tool.calls) == 2
    event_id = first.entry.committed.event.event_id
    before = repo.get_state("test")
    repo.fork("test", branch_id="alternate", at_event_id=event_id)
    # Reopen without any external adapter, as a read-only consumer would.
    restarted = SQLiteWorldRepository(tape.path)
    for _ in range(10):
        assert restarted.dispatch("test", attack(idempotency_key="key")).entry == first.entry
        entry = restarted.get_event("test", event_id)
        await FallbackNarrator().render(
            NarrationRequest(
                world_id="test",
                event=entry.committed.event,
                result=entry.result,
            )
        )
        assert restarted.replay("test") == before
    assert restarted.dispatch("test", attack(idempotency_key="key"), branch_id="alternate").replayed
    assert len(restarted.entries("test")) == 1
    assert len(tool.calls) == 2
    repo.dispatch("test", attack(event_id="branch-next"), branch_id="alternate")
    assert len(tool.calls) == 4
    assert restarted.get_state("test") == before


def test_external_execution_requires_stable_event_identity(tape):
    tool = FakeTool()
    repo = SQLiteWorldRepository(tape.path, external_dice=binding(tool))
    with pytest.raises(ValueError, match="event_id or idempotency_key"):
        repo.dispatch("test", attack())
    assert tool.calls == []


def test_event_commit_marks_only_consumed_tool_results(tape):
    tool = FakeTool()
    unrelated = call(event_id="turn")
    tape.execute(unrelated, tool)
    repo = SQLiteWorldRepository(tape.path, external_dice=binding(tool))
    repo.dispatch("test", attack(event_id="turn"))
    assert tape.get(unrelated).status == "completed"
    with sqlite3.connect(tape.path) as connection:
        assert connection.execute(
            "SELECT tool_call_key FROM tool_executions WHERE status = 'committed' ORDER BY 1"
        ).fetchall() == [("attack",), ("damage",)]


def test_invalid_dice_result_is_persisted_but_never_consumed_or_rerolled(tape):
    class InvalidDice(FakeTool):
        def execute(self, call, *, idempotency_key):
            result = super().execute(call, idempotency_key=idempotency_key)
            result["roll_key"] = "wrong-key"
            return result

    tool = InvalidDice()
    repo = SQLiteWorldRepository(tape.path, external_dice=binding(tool))
    for _ in range(2):
        with pytest.raises(ValueError, match="does not match"):
            repo.dispatch("test", attack(event_id="turn"))
    assert len(tool.calls) == 1
    assert repo.get_state("test") == initial_state()
    assert repo.entries("test") == []


def test_api_reports_pending_and_conflicting_external_calls(tape):
    from fastapi.testclient import TestClient

    from saga_trpg.api.app import create_app

    class TimeoutTool(FakeTool):
        def execute(self, call, *, idempotency_key):
            raise TimeoutError("lost remote response")

    repo = SQLiteWorldRepository(tape.path, external_dice=binding(TimeoutTool()))
    with pytest.raises(TimeoutError):
        repo.dispatch("test", attack(event_id="turn"))
    with TestClient(create_app(repository=repo)) as client:
        response = client.post("/worlds/test/events", json=attack(event_id="turn").model_dump())
        assert response.status_code == 503
        assert "ambiguous" in response.json()["detail"]
        changed = attack(event_id="turn")
        changed.payload["target_id"] = "other"
        assert client.post("/worlds/test/events", json=changed.model_dump()).status_code == 409
        assert client.get("/worlds/test/state").json()["seq"] == 0


def test_crash_after_remote_effect_does_not_repeat_in_fresh_process(tape, tmp_path):
    counter = tmp_path / "effects.txt"
    code = """
import os, sys
from pathlib import Path
from saga_trpg.adapters.storage.tool_tape import SQLiteToolResultTape
from saga_trpg.domain.tools import ToolCall
class Tool:
    def execute(self, call, *, idempotency_key):
        with Path(sys.argv[2]).open('a') as stream:
            stream.write('effect\\n')
        os._exit(73)
SQLiteToolResultTape(sys.argv[1]).execute(ToolCall.model_validate_json(sys.argv[3]), Tool())
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(tape.path), str(counter), call().model_dump_json()],
        check=False,
        timeout=10,
    )
    assert child.returncode == 73
    tool = FakeTool()
    with pytest.raises(ToolExecutionPending):
        SQLiteToolResultTape(tape.path).execute(call(), tool)
    assert counter.read_text().splitlines() == ["effect"]
    assert tool.calls == []


def test_concurrent_processes_and_restart_reuse_one_generic_result(tape, tmp_path):
    counter = tmp_path / "effects.txt"
    code = """
import sys
from pathlib import Path
from saga_trpg.adapters.storage.tool_tape import SQLiteToolResultTape
from saga_trpg.domain.tools import ToolCall
from saga_trpg.ports.tools import ToolExecutionPending
class Tool:
    def execute(self, call, *, idempotency_key):
        with Path(sys.argv[2]).open('a') as stream:
            stream.write('effect\\n')
        return {'result': 42}
try:
    result = SQLiteToolResultTape(sys.argv[1]).execute(
        ToolCall.model_validate_json(sys.argv[3]), Tool())
    assert result.result == {'result': 42}
except ToolExecutionPending:
    sys.exit(75)
"""
    command = [sys.executable, "-c", code, str(tape.path), str(counter), call().model_dump_json()]
    children = [subprocess.Popen(command) for _ in range(6)]
    statuses = [child.wait(timeout=15) for child in children]
    assert 0 in statuses
    assert set(statuses) <= {0, 75}
    subprocess.run(command, check=True, timeout=10)
    assert counter.read_text().splitlines() == ["effect"]
    assert tape.get(call()).result == {"result": 42}


def test_crash_before_event_commit_reuses_results_in_fresh_process(tape, tmp_path):
    counter = tmp_path / "effects.txt"
    code = """
import os, sys
from pathlib import Path
from saga_trpg.adapters.storage.sqlite import SQLiteJournal, SQLiteWorldRepository
from saga_trpg.domain.events import GameEvent
from saga_trpg.ports.tools import ToolBinding
from saga_trpg.runtime.rng import DeterministicDice
class Tool:
    def execute(self, call, *, idempotency_key):
        with Path(sys.argv[2]).open('a') as stream:
            stream.write(call.tool_call_key + '\\n')
        return DeterministicDice().roll(
            world_id=call.world_id, event_id=call.event_id,
            roll_key=call.arguments['roll_key'], expression=call.arguments['expression'],
        ).model_dump()
if sys.argv[3] == 'crash':
    original = SQLiteJournal.append
    def crash(self, entry):
        original(self, entry)
        os._exit(73)
    SQLiteJournal.append = crash
repo = SQLiteWorldRepository(sys.argv[1], external_dice=ToolBinding('dice', '1', 'fake-1', Tool()))
outcome = repo.dispatch('test', GameEvent(kind='attack', actor_id='player', idempotency_key='key',
    payload={'target_id':'goblin','attack_bonus':20,'target_ac':10,'damage':'1d6+2'}))
assert repo.replay('test') == outcome.entry.result.state
assert len(repo.entries('test')) == 1
"""
    command = [sys.executable, "-c", code, str(tape.path), str(counter)]
    assert subprocess.run([*command, "crash"], check=False, timeout=10).returncode == 73
    assert SQLiteWorldRepository(tape.path).get_state("test").seq == 0
    subprocess.run([*command, "resume"], check=True, timeout=10)
    assert counter.read_text().splitlines() == ["attack", "damage"]
