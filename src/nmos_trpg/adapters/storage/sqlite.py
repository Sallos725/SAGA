from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from nmos_trpg.domain.branches import BranchInfo, ForkRequest
from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import WorldState
from nmos_trpg.domain.rulesets import RulesetReference
from nmos_trpg.modules.base import RuleModule
from nmos_trpg.modules.catalog import RulesetRegistry
from nmos_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from nmos_trpg.ports.dice import DicePort
from nmos_trpg.runtime.engine import DispatchOutcome, RuntimeEngine
from nmos_trpg.runtime.hashing import stable_model_hash
from nmos_trpg.runtime.journal import JournalEntry
from nmos_trpg.runtime.replay import ReplayError, replay_recorded_results
from nmos_trpg.runtime.rng import DeterministicDice

STORAGE_VERSION = 1
RUNTIME_VERSION = "0.2.0"


class RepositoryConflict(ValueError):
    """The requested world or branch already exists."""


class RepositoryNotFound(LookupError):
    """The requested world, branch, or fork event does not exist."""


class RepositoryVersionError(ValueError):
    """Stored data requires a different runtime or an explicit migration."""


_SCHEMA = (
    """CREATE TABLE worlds (
        world_id TEXT PRIMARY KEY,
        initial_state_json TEXT NOT NULL,
        seed TEXT NOT NULL,
        ruleset TEXT NOT NULL,
        ruleset_version TEXT NOT NULL,
        runtime_version TEXT NOT NULL
    )""",
    """CREATE TABLE branches (
        world_id TEXT NOT NULL REFERENCES worlds(world_id),
        branch_id TEXT NOT NULL,
        parent_branch_id TEXT,
        fork_event_id TEXT,
        fork_seq INTEGER NOT NULL CHECK(fork_seq >= 0),
        PRIMARY KEY (world_id, branch_id),
        FOREIGN KEY (world_id, parent_branch_id) REFERENCES branches(world_id, branch_id)
    )""",
    """CREATE TABLE entries (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq > 0),
        event_id TEXT NOT NULL,
        idempotency_key TEXT,
        entry_json TEXT NOT NULL,
        PRIMARY KEY (world_id, branch_id, seq),
        UNIQUE (world_id, branch_id, event_id),
        UNIQUE (world_id, branch_id, idempotency_key),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id)
    )""",
    """CREATE TABLE snapshots (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq >= 0),
        state_json TEXT NOT NULL,
        state_hash TEXT NOT NULL,
        PRIMARY KEY (world_id, branch_id, seq),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id)
    )""",
)


class SQLiteJournal:
    """Journal bound to a repository transaction, never an independently committed write."""

    def __init__(self, connection: sqlite3.Connection, world_id: str, branch_id: str):
        self.connection = connection
        self.scope = (world_id, branch_id)

    def _decode(self, row: sqlite3.Row) -> JournalEntry:
        try:
            entry = JournalEntry.model_validate_json(row["entry_json"])
            event = entry.committed.event
            if (
                entry.committed.seq != row["seq"]
                or entry.result.state.seq != row["seq"]
                or entry.result.state.world_id != self.scope[0]
                or event.event_id != row["event_id"]
                or event.idempotency_key != row["idempotency_key"]
                or not entry.result.trace
                or stable_model_hash(entry.result.state) != entry.committed.state_hash_after
            ):
                raise ValueError("journal columns or state hash do not match the record")
            return entry
        except ValueError as exc:
            raise ReplayError("invalid stored journal entry") from exc

    def append(self, entry: JournalEntry) -> None:
        self.connection.execute(
            "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)",
            (
                *self.scope,
                entry.committed.seq,
                entry.committed.event.event_id,
                entry.committed.event.idempotency_key,
                entry.model_dump_json(),
            ),
        )

    def entries(self, *, after_seq: int = 0, through_seq: int | None = None) -> list[JournalEntry]:
        rows = self.connection.execute(
            """SELECT * FROM entries WHERE world_id = ? AND branch_id = ? AND seq > ?
               AND (? IS NULL OR seq <= ?) ORDER BY seq""",
            (*self.scope, after_seq, through_seq, through_seq),
        )
        return [self._decode(row) for row in rows]

    def find_by_event_id(self, event_id: str) -> JournalEntry | None:
        row = self.connection.execute(
            "SELECT * FROM entries WHERE world_id = ? AND branch_id = ? AND event_id = ?",
            (*self.scope, event_id),
        ).fetchone()
        return self._decode(row) if row else None

    def find_by_idempotency_key(self, key: str) -> JournalEntry | None:
        row = self.connection.execute(
            "SELECT * FROM entries WHERE world_id = ? AND branch_id = ? AND idempotency_key = ?",
            (*self.scope, key),
        ).fetchone()
        return self._decode(row) if row else None


class SQLiteWorldRepository:
    """Persistent outer boundary. All rules still execute through RuntimeEngine and ports."""

    def __init__(
        self,
        path: str | Path,
        *,
        rules: RuleModule | None = None,
        dice: DicePort | None = None,
        seed: str = "dev-seed",
        snapshot_interval: int = 50,
    ) -> None:
        if snapshot_interval < 1:
            raise ValueError("snapshot_interval must be positive")
        if str(path) == ":memory:":
            raise ValueError("a persistent database file is required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rules = rules if rules is not None else GenericFantasyRules()
        self._explicit_rules = rules
        self.registry = RulesetRegistry()
        self.dice = dice
        self.seed = seed
        self.snapshot_interval = snapshot_interval
        with self._transaction(write=True, initialize=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise RepositoryVersionError("unversioned database requires explicit migration")
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {STORAGE_VERSION}")
            elif version != STORAGE_VERSION:
                raise RepositoryVersionError(f"unsupported storage version: {version}")

    @contextmanager
    def _transaction(
        self, *, write: bool = False, initialize: bool = False
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            # Acquire the write lock before idempotency checks or tool execution.
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if not initialize:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version != STORAGE_VERSION:
                    raise RepositoryVersionError(f"unsupported storage version: {version}")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _world(self, connection: sqlite3.Connection, world_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM worlds WHERE world_id = ?", (world_id,)).fetchone()
        if row is None:
            raise RepositoryNotFound("world not found")
        if row["runtime_version"] != RUNTIME_VERSION:
            raise RepositoryVersionError("world ruleset/runtime version is incompatible")
        try:
            self._select_rules(row["ruleset"], row["ruleset_version"])
        except ValueError as exc:
            raise RepositoryVersionError("world ruleset/runtime version is incompatible") from exc
        return row

    def _select_rules(self, ruleset_id: str, version: str) -> RuleModule:
        if self._explicit_rules is not None:
            if (ruleset_id, version) != (self._explicit_rules.name, self._explicit_rules.version):
                raise ValueError("ruleset/version differs from the explicitly injected rules")
            return self._explicit_rules
        return self.registry.get(ruleset_id, version).rules

    def ruleset_for_world(self, world_id: str) -> RulesetReference:
        with self._transaction() as connection:
            world = self._world(connection, world_id)
            return RulesetReference(
                ruleset_id=world["ruleset"], ruleset_version=world["ruleset_version"]
            )

    def _branch(self, connection: sqlite3.Connection, world_id: str, branch_id: str) -> BranchInfo:
        row = connection.execute(
            "SELECT * FROM branches WHERE world_id = ? AND branch_id = ?", (world_id, branch_id)
        ).fetchone()
        if row is None:
            raise RepositoryNotFound("branch not found")
        return BranchInfo.model_validate(dict(row))

    def _snapshot(self, connection: sqlite3.Connection, branch_id: str, state: WorldState) -> None:
        connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            (
                state.world_id,
                branch_id,
                state.seq,
                state.model_dump_json(),
                stable_model_hash(state),
            ),
        )

    def create_world(
        self,
        initial: WorldState,
        *,
        if_missing: bool = False,
        ruleset_id: str | None = None,
        ruleset_version: str | None = None,
    ) -> bool:
        initial = WorldState.model_validate(initial.model_dump())
        if initial.seq != 0:
            raise ValueError("initial world sequence must be zero")
        if (ruleset_id is None) != (ruleset_version is None):
            raise ValueError("ruleset id and version must be specified together")
        rules = self._select_rules(
            ruleset_id if ruleset_id is not None else self.rules.name,
            ruleset_version if ruleset_version is not None else self.rules.version,
        )
        with self._transaction(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM worlds WHERE world_id = ?", (initial.world_id,)
            ).fetchone():
                self._world(connection, initial.world_id)
                if if_missing:
                    return False
                raise RepositoryConflict("world already exists")
            connection.execute(
                "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?)",
                (
                    initial.world_id,
                    initial.model_dump_json(),
                    self.seed,
                    rules.name,
                    rules.version,
                    RUNTIME_VERSION,
                ),
            )
            connection.execute(
                "INSERT INTO branches VALUES (?, 'main', NULL, NULL, 0)", (initial.world_id,)
            )
            self._snapshot(connection, "main", initial)
        return True

    def _load_state(
        self,
        connection: sqlite3.Connection,
        world_id: str,
        branch_id: str,
        *,
        through_seq: int | None = None,
        use_snapshots: bool = True,
    ) -> WorldState:
        world = self._world(connection, world_id)
        self._branch(connection, world_id, branch_id)
        journal = SQLiteJournal(connection, world_id, branch_id)
        head = connection.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM entries WHERE world_id = ? AND branch_id = ?",
            (world_id, branch_id),
        ).fetchone()[0]
        target = head if through_seq is None else through_seq
        if not 0 <= target <= head:
            raise ReplayError("requested sequence is outside the committed history")
        try:
            state = WorldState.model_validate_json(world["initial_state_json"])
            if state.world_id != world_id or state.seq != 0:
                raise ValueError("invalid initial world")
            if use_snapshots:
                snapshot = connection.execute(
                    """SELECT * FROM snapshots WHERE world_id = ? AND branch_id = ? AND seq <= ?
                       ORDER BY seq DESC LIMIT 1""",
                    (world_id, branch_id, target),
                ).fetchone()
                if snapshot is None:
                    raise ValueError("missing snapshot")
                saved = WorldState.model_validate_json(snapshot["state_json"])
                if (
                    saved.world_id != world_id
                    or saved.seq != snapshot["seq"]
                    or stable_model_hash(saved) != snapshot["state_hash"]
                ):
                    raise ValueError("invalid snapshot hash or identity")
                if saved.seq == 0:
                    if saved != state:
                        raise ValueError("snapshot differs from initial state")
                else:
                    boundary = connection.execute(
                        "SELECT * FROM entries WHERE world_id = ? AND branch_id = ? AND seq = ?",
                        (world_id, branch_id, saved.seq),
                    ).fetchone()
                    if boundary is None or journal._decode(boundary).result.state != saved:
                        raise ValueError("snapshot differs from committed state")
                state = saved
            state = replay_recorded_results(
                state, journal.entries(after_seq=state.seq, through_seq=target)
            )
            if state.seq != target:
                raise ValueError("incomplete journal prefix")
            return state
        except ValueError as exc:
            raise ReplayError("invalid stored state or transition history") from exc

    def get_state(self, world_id: str, *, branch_id: str = "main") -> WorldState:
        with self._transaction() as connection:
            return self._load_state(connection, world_id, branch_id)

    def dispatch(
        self, world_id: str, event: GameEvent, *, branch_id: str = "main"
    ) -> DispatchOutcome:
        with self._transaction(write=True) as connection:
            state = self._load_state(connection, world_id, branch_id)
            world = self._world(connection, world_id)
            engine = RuntimeEngine(
                initial_state=state,
                rules=self._select_rules(world["ruleset"], world["ruleset_version"]),
                dice=self.dice if self.dice is not None else DeterministicDice(world["seed"]),
                journal=SQLiteJournal(connection, world_id, branch_id),
                event_id_prefix=world_id
                if branch_id == "main"
                else f"{world_id}:branch:{branch_id}",
            )
            outcome = engine.dispatch(event)
            if not outcome.replayed and engine.state.seq % self.snapshot_interval == 0:
                self._snapshot(connection, branch_id, engine.state)
        # Only publish the result after the transaction has committed successfully.
        return outcome

    def get_event(
        self, world_id: str, event_id: str, *, branch_id: str = "main"
    ) -> JournalEntry | None:
        with self._transaction() as connection:
            self._world(connection, world_id)
            self._branch(connection, world_id, branch_id)
            return SQLiteJournal(connection, world_id, branch_id).find_by_event_id(event_id)

    def entries(self, world_id: str, *, branch_id: str = "main") -> list[JournalEntry]:
        with self._transaction() as connection:
            self._world(connection, world_id)
            self._branch(connection, world_id, branch_id)
            return SQLiteJournal(connection, world_id, branch_id).entries()

    def replay(
        self, world_id: str, *, branch_id: str = "main", at_event_id: str | None = None
    ) -> WorldState:
        with self._transaction() as connection:
            through_seq = None
            if at_event_id is not None:
                entry = SQLiteJournal(connection, world_id, branch_id).find_by_event_id(at_event_id)
                if entry is None:
                    raise RepositoryNotFound("event not found")
                through_seq = entry.committed.seq
            return self._load_state(
                connection, world_id, branch_id, through_seq=through_seq, use_snapshots=False
            )

    def branches(self, world_id: str) -> list[BranchInfo]:
        with self._transaction() as connection:
            self._world(connection, world_id)
            rows = connection.execute(
                "SELECT * FROM branches WHERE world_id = ? ORDER BY branch_id", (world_id,)
            )
            return [BranchInfo.model_validate(dict(row)) for row in rows]

    def fork(
        self,
        world_id: str,
        *,
        branch_id: str,
        at_event_id: str | None,
        source_branch_id: str = "main",
    ) -> BranchInfo:
        request = ForkRequest(
            branch_id=branch_id, source_branch_id=source_branch_id, at_event_id=at_event_id
        )
        with self._transaction(write=True) as connection:
            self._world(connection, world_id)
            self._branch(connection, world_id, request.source_branch_id)
            if connection.execute(
                "SELECT 1 FROM branches WHERE world_id = ? AND branch_id = ?", (world_id, branch_id)
            ).fetchone():
                raise RepositoryConflict("branch already exists")
            fork_seq = 0
            if at_event_id is not None:
                entry = SQLiteJournal(connection, world_id, source_branch_id).find_by_event_id(
                    at_event_id
                )
                if entry is None:
                    raise RepositoryNotFound("fork event not found")
                fork_seq = entry.committed.seq
            state = self._load_state(
                connection, world_id, source_branch_id, through_seq=fork_seq, use_snapshots=False
            )
            connection.execute(
                "INSERT INTO branches VALUES (?, ?, ?, ?, ?)",
                (world_id, branch_id, source_branch_id, at_event_id, fork_seq),
            )
            # Copy the immutable prefix verbatim, including roll records and idempotency identities.
            connection.execute(
                """INSERT INTO entries
                   SELECT world_id, ?, seq, event_id, idempotency_key, entry_json
                   FROM entries WHERE world_id = ? AND branch_id = ? AND seq <= ?""",
                (branch_id, world_id, source_branch_id, fork_seq),
            )
            self._snapshot(connection, branch_id, state)
            branch = self._branch(connection, world_id, branch_id)
        return branch
