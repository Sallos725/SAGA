import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from saga_trpg.adapters.storage.sqlite import (
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.runtime.rng import DeterministicDice


class CountingDice(DeterministicDice):
    def __init__(self):
        super().__init__("fixed-seed")
        self.calls = 0

    def roll(self, **kwargs):
        self.calls += 1
        return super().roll(**kwargs)


def initial_state(world_id="test"):
    return WorldState(
        world_id=world_id,
        actors={
            "player": ActorState(actor_id="player", hp=20, max_hp=20),
            "goblin": ActorState(actor_id="goblin", hp=12, max_hp=12),
        },
    )


def attack(**kwargs):
    return GameEvent(
        kind="attack",
        actor_id="player",
        payload={"target_id": "goblin", "attack_bonus": 20, "target_ac": 10, "damage": "1d6+2"},
        **kwargs,
    )


@pytest.fixture
def repository(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "worlds.sqlite3", snapshot_interval=2)
    repo.create_world(initial_state())
    return repo


def test_restart_replay_and_repeated_attack_do_not_roll_again(repository):
    dice = CountingDice()
    repository.dice = dice
    event = attack(event_id="turn-1", idempotency_key="request-1")
    first = repository.dispatch("test", event)
    repository.dispatch("test", GameEvent(kind="time_advance", actor_id="player"))
    repository.dispatch("test", GameEvent(kind="time_advance", actor_id="player"))
    before = repository.get_state("test")

    restarted = SQLiteWorldRepository(repository.path, dice=dice)
    assert restarted.get_state("test") == before
    assert restarted.replay("test") == before
    for _ in range(10):
        outcome = restarted.dispatch("test", event)
        assert outcome.replayed
        assert outcome.entry == first.entry
        assert restarted.get_event("test", "turn-1") == first.entry
    assert dice.calls == 2
    assert restarted.get_state("test") == before
    assert len(restarted.entries("test")) == 3
    with sqlite3.connect(repository.path) as conn:
        assert conn.execute("SELECT seq FROM snapshots ORDER BY seq").fetchall() == [(0,), (2,)]


def test_seed_is_persisted_for_future_actions(repository, tmp_path):
    repository.dispatch("test", attack())
    restarted = SQLiteWorldRepository(repository.path, seed="a-new-environment-seed")
    reference = SQLiteWorldRepository(tmp_path / "reference.sqlite3")
    reference.create_world(initial_state())
    reference.dispatch("test", attack())
    assert restarted.dispatch("test", attack()).entry == reference.dispatch("test", attack()).entry


def test_fork_rewinds_preserves_history_and_isolates_new_events(repository):
    first = repository.dispatch("test", attack(idempotency_key="turn-1"))
    repository.dispatch("test", GameEvent(kind="time_advance", actor_id="player"))
    original = repository.get_state("test")
    original_entries = repository.entries("test")
    branch = repository.fork(
        "test", branch_id="alternate", at_event_id=first.entry.committed.event.event_id
    )

    assert branch.parent_branch_id == "main"
    assert branch.fork_seq == 1
    assert repository.get_state("test", branch_id="alternate") == first.entry.result.state
    repeated = repository.dispatch("test", attack(idempotency_key="turn-1"), branch_id="alternate")
    assert repeated.replayed
    forked = repository.dispatch("test", attack(), branch_id="alternate")
    assert forked.entry.committed.event.event_id != original_entries[-1].committed.event.event_id
    assert repository.get_state("test") == original
    assert repository.entries("test") == original_entries

    restarted = SQLiteWorldRepository(repository.path)
    assert restarted.get_state("test", branch_id="alternate") == forked.entry.result.state
    assert restarted.replay("test", branch_id="alternate") == forked.entry.result.state
    assert (
        restarted.replay("test", at_event_id=first.entry.committed.event.event_id)
        == first.entry.result.state
    )


def test_dispatch_preconditions_guard_only_new_commits(repository):
    from saga_trpg.adapters.storage.sqlite import RepositoryConflict

    repository.fork("test", branch_id="other", at_event_id=None)
    first = repository.dispatch("test", attack(event_id="test:e1"), branch_id="other")
    # The check covers the id that will be committed, so a generated one must be explicit.
    with pytest.raises(ValueError, match="explicit event_id"):
        repository.dispatch("test", attack(), unique_in_world=True)
    with pytest.raises(ValueError, match="explicit event_id"):
        repository.dispatch("test", attack(idempotency_key="key"), unique_in_world=True)
    with pytest.raises(RepositoryConflict, match="another branch"):
        repository.dispatch("test", attack(event_id="test:e1"), unique_in_world=True)
    with pytest.raises(RepositoryConflict, match="head changed"):
        repository.dispatch("test", attack(event_id="late"), branch_id="other", expected_seq=0)
    assert repository.entries("test") == []
    retried = repository.dispatch(
        "test", attack(event_id="test:e1"), branch_id="other", expected_seq=0, unique_in_world=True
    )
    assert retried.replayed and retried.entry == first.entry
    assert repository.dispatch(
        "test", attack(event_id="mine"), expected_seq=0, unique_in_world=True
    )


def test_fork_refuses_an_action_that_is_already_committed(repository):
    from saga_trpg.adapters.storage.sqlite import RepositoryConflict

    repository.dispatch("test", attack(event_id="mine"))
    branches = repository.branches("test")
    with pytest.raises(RepositoryConflict, match="already committed"):
        repository.fork("test", branch_id="late", at_event_id=None, unless_committed="mine")
    assert repository.branches("test") == branches
    assert repository.fork("test", branch_id="late", at_event_id=None, unless_committed="new")


def test_fork_at_genesis_and_duplicate_branch_rejected(repository):
    repository.dispatch("test", attack())
    repository.fork("test", branch_id="empty", at_event_id=None)
    assert repository.get_state("test", branch_id="empty") == initial_state()
    assert repository.entries("test", branch_id="empty") == []
    with pytest.raises(ValueError, match="already exists"):
        repository.fork("test", branch_id="empty", at_event_id=None)


def test_concurrent_repository_instances_execute_tools_once(repository):
    dice = CountingDice()
    workers = [SQLiteWorldRepository(repository.path, dice=dice) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda repo: repo.dispatch("test", attack(idempotency_key="same")), workers)
        )
    assert sum(not result.replayed for result in results) == 1
    assert dice.calls == 2
    assert len(repository.entries("test")) == 1


def test_world_and_branch_identity_scopes(repository):
    repository.create_world(initial_state("other"))
    repository.dispatch("test", attack(event_id="shared"))
    repository.dispatch("other", attack(event_id="shared"))
    assert len(repository.entries("test")) == len(repository.entries("other")) == 1
    with pytest.raises(LookupError):
        repository.fork("test", branch_id="invalid", at_event_id="missing")
    with pytest.raises(LookupError):
        repository.get_state("test", branch_id="invalid")


def test_snapshot_failure_rolls_back_journal_and_state(repository):
    repository.dispatch("test", attack())
    before = repository.get_state("test")
    with sqlite3.connect(repository.path) as conn:
        conn.execute("""CREATE TRIGGER fail_snapshot BEFORE INSERT ON snapshots
                        BEGIN SELECT RAISE(ABORT, 'injected snapshot failure'); END""")
    with pytest.raises(sqlite3.DatabaseError, match="injected snapshot failure"):
        repository.dispatch("test", attack(event_id="failed"))
    restarted = SQLiteWorldRepository(repository.path)
    assert restarted.get_state("test") == before
    assert restarted.get_event("test", "failed") is None
    assert len(restarted.entries("test")) == 1


def test_process_exit_mid_transaction_recovers_committed_prefix(repository):
    repository.dispatch("test", attack(event_id="committed"))
    before = repository.get_state("test")
    code = """
import os, sqlite3, sys
from saga_trpg.adapters.storage.sqlite import (
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
from saga_trpg.domain.events import GameEvent
from saga_trpg.modules.generic_fantasy.rules import GenericFantasyRules
class CrashRules(GenericFantasyRules):
    def apply(self, **kwargs):
        os._exit(73)
repo = SQLiteWorldRepository(sys.argv[1], rules=CrashRules())
repo.dispatch('test', GameEvent(event_id='uncommitted', kind='time_advance', actor_id='player'))
"""
    process = subprocess.run([sys.executable, "-c", code, str(repository.path)], check=False)
    assert process.returncode == 73
    assert SQLiteWorldRepository(repository.path).get_state("test") == before
    assert len(repository.entries("test")) == 1


def test_process_exit_after_insert_before_commit_recovers_prefix(repository):
    repository.dispatch("test", attack(event_id="committed"))
    before = repository.get_state("test")
    code = """
import os, sys
from saga_trpg.adapters.storage.sqlite import SQLiteJournal, SQLiteWorldRepository
from saga_trpg.domain.events import GameEvent
original_append = SQLiteJournal.append
def crash_after_insert(self, entry):
    original_append(self, entry)
    assert self.find_by_event_id('uncommitted') is not None
    os._exit(73)
SQLiteJournal.append = crash_after_insert
repo = SQLiteWorldRepository(sys.argv[1])
repo.dispatch('test', GameEvent(event_id='uncommitted', kind='time_advance', actor_id='player'))
"""
    process = subprocess.run([sys.executable, "-c", code, str(repository.path)], check=False)
    assert process.returncode == 73
    restarted = SQLiteWorldRepository(repository.path)
    assert restarted.get_state("test") == before
    assert restarted.get_event("test", "uncommitted") is None
    assert len(restarted.entries("test")) == 1


def test_exit_after_commit_before_response_retry_reuses_tools(repository):
    code = """
import os, sys
from saga_trpg.adapters.storage.sqlite import (
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
from saga_trpg.domain.events import GameEvent
repo = SQLiteWorldRepository(sys.argv[1])
repo.dispatch('test', GameEvent(kind='attack', actor_id='player', idempotency_key='lost-response',
    payload={'target_id':'goblin', 'attack_bonus':20, 'target_ac':10, 'damage':'1d6+2'}))
os._exit(74)
"""
    process = subprocess.run([sys.executable, "-c", code, str(repository.path)], check=False)
    assert process.returncode == 74
    dice = CountingDice()
    restarted = SQLiteWorldRepository(repository.path, dice=dice)
    assert restarted.dispatch("test", attack(idempotency_key="lost-response")).replayed
    assert dice.calls == 0
    assert len(restarted.entries("test")) == 1


@pytest.mark.asyncio
async def test_rerender_reads_recorded_result_without_mutation(repository):
    from saga_trpg.adapters.narrator.fallback import FallbackNarrator
    from saga_trpg.ports.narrator import NarrationRequest

    dice = CountingDice()
    repository.dice = dice
    repository.dispatch("test", attack(event_id="narrate"))
    before_state = repository.get_state("test")
    before_entries = repository.entries("test")
    for _ in range(10):
        entry = repository.get_event("test", "narrate")
        request = NarrationRequest(
            world_id="test", event=entry.committed.event, result=entry.result
        )
        assert await FallbackNarrator().render(request)
        # Even a consumer that changes its own copy cannot reach authoritative persisted state.
        request.result.state.actors["goblin"].hp = 999
    assert repository.get_state("test") == before_state
    assert repository.entries("test") == before_entries
    assert dice.calls == 2


@pytest.mark.parametrize("target", ["snapshot", "journal"])
def test_corrupt_persistent_data_is_rejected(repository, target):
    repository.dispatch("test", attack())
    repository.dispatch("test", attack())
    with sqlite3.connect(repository.path) as conn:
        if target == "snapshot":
            conn.execute("UPDATE snapshots SET state_hash = 'wrong' WHERE seq = 2")
        else:
            conn.execute("UPDATE entries SET entry_json = '{}' WHERE seq = 1")
    with pytest.raises(ValueError):
        if target == "snapshot":
            repository.get_state("test")
        else:
            repository.replay("test")


def test_unsupported_storage_version_is_rejected(repository):
    with sqlite3.connect(repository.path) as conn:
        conn.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="version"):
        SQLiteWorldRepository(repository.path)


def test_unsupported_ruleset_version_is_rejected(repository):
    with sqlite3.connect(repository.path) as conn:
        conn.execute("UPDATE worlds SET ruleset_version = 'future-version'")
    with pytest.raises(ValueError, match="version"):
        repository.dispatch("test", attack())


def test_conflicting_persisted_identity_is_rejected_before_tools(repository):
    dice = CountingDice()
    repository.dice = dice
    repository.dispatch("test", attack(event_id="first", idempotency_key="request-1"))
    repository.dispatch("test", attack(event_id="second", idempotency_key="request-2"))
    before = repository.get_state("test")
    with pytest.raises(ValueError, match="conflict"):
        repository.dispatch("test", attack(event_id="first", idempotency_key="request-2"))
    with pytest.raises(ValueError, match="conflict"):
        repository.dispatch(
            "test", GameEvent(kind="time_advance", actor_id="player", event_id="first")
        )
    assert repository.get_state("test") == before
    assert dice.calls == 4


def test_fork_of_fork_keeps_provenance_and_world_independence(repository):
    repository.dispatch("test", attack(event_id="first"))
    repository.fork("test", branch_id="a", at_event_id="first")
    repository.dispatch("test", attack(event_id="second"), branch_id="a")
    branch = repository.fork("test", branch_id="b", source_branch_id="a", at_event_id="second")
    assert branch.parent_branch_id == "a"
    assert branch.fork_event_id == "second"
    assert repository.get_state("test", branch_id="b") == repository.get_state(
        "test", branch_id="a"
    )
    assert repository.entries("test", branch_id="b") == repository.entries("test", branch_id="a")
    assert len(repository.entries("test")) == 1


def test_concurrent_processes_do_not_repeat_committed_dice(repository, tmp_path):
    counter = tmp_path / "dice-calls.txt"
    code = """
import sys
from pathlib import Path
from saga_trpg.adapters.storage.sqlite import (
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
from saga_trpg.runtime.rng import DeterministicDice
from saga_trpg.domain.events import GameEvent
class LoggedDice(DeterministicDice):
    def roll(self, **kwargs):
        with Path(sys.argv[2]).open('a') as stream:
            stream.write(kwargs['roll_key'] + '\\n')
        return super().roll(**kwargs)
repo = SQLiteWorldRepository(sys.argv[1], dice=LoggedDice())
repo.dispatch('test', GameEvent(kind='attack', actor_id='player', idempotency_key='concurrent',
    payload={'target_id':'goblin', 'attack_bonus':20, 'target_ac':10, 'damage':'1d6+2'}))
"""
    children = [
        subprocess.Popen([sys.executable, "-c", code, str(repository.path), str(counter)])
        for _ in range(4)
    ]
    for child in children:
        assert child.wait(timeout=15) == 0
    assert counter.read_text().splitlines() == ["attack", "damage"]
    assert len(repository.entries("test")) == 1


def test_recovery_in_fresh_process_with_different_hash_seed(repository):
    world = initial_state("conditions")
    world.actors["player"].conditions = {"poisoned", "stunned", "hidden", "blessed"}
    repository.create_world(world)
    repository.dispatch("conditions", attack())
    code = """
import sys
from saga_trpg.adapters.storage.sqlite import (
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
repo = SQLiteWorldRepository(sys.argv[1])
assert repo.get_state('conditions') == repo.replay('conditions')
assert repo.get_state('conditions').seq == 1
"""
    subprocess.run(
        [sys.executable, "-c", code, str(repository.path)],
        env={**os.environ, "PYTHONHASHSEED": "42"},
        check=True,
    )


def test_initial_state_read_ignores_moving_heads_and_snapshots(repository):
    original = initial_state()
    repository.dispatch("test", attack())
    repository.dispatch("test", GameEvent(kind="time_advance", actor_id="player"))
    assert repository.get_state("test").seq == 2
    assert repository.get_initial_state("test") == original
    reread = SQLiteWorldRepository(repository.path).get_initial_state("test")
    assert reread == original
    reread.actors["goblin"].hp = 0
    assert repository.get_initial_state("test") == original


def test_discard_empty_branch_removes_only_uncommitted_forks(repository):
    repository.dispatch("test", attack(event_id="first"))
    repository.fork("test", branch_id="empty", at_event_id="first")
    repository.fork("test", branch_id="used", at_event_id="first")
    repository.dispatch("test", attack(event_id="second"), branch_id="used")
    with pytest.raises(RepositoryConflict):
        repository.discard_empty_branch("test", "used")
    with pytest.raises(RepositoryConflict):
        repository.discard_empty_branch("test", "main")
    with pytest.raises(RepositoryNotFound):
        repository.discard_empty_branch("test", "missing")
    repository.discard_empty_branch("test", "empty")
    assert [item.branch_id for item in repository.branches("test")] == ["main", "used"]
    assert len(repository.entries("test", branch_id="used")) == 2
    assert repository.get_state("test", branch_id="main") == repository.get_state(
        "test", branch_id="main"
    )
    # The name is free again and a new fork from the same point starts clean.
    repository.fork("test", branch_id="empty", at_event_id="first")
    assert len(repository.entries("test", branch_id="empty")) == 1
