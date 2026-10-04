from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from saga_trpg.adapters.dice.recorded import RecordedDice, ToolResultRequired
from saga_trpg.adapters.storage.database import RepositoryVersionError, SQLiteDatabase
from saga_trpg.adapters.storage.tool_tape import SQLiteToolResultTape
from saga_trpg.domain.branches import BranchInfo, ForkRequest
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import WorldState
from saga_trpg.domain.rulesets import RulesetReference
from saga_trpg.modules.base import RuleModule
from saga_trpg.modules.catalog import RulesetRegistry
from saga_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from saga_trpg.ports.dice import DicePort
from saga_trpg.ports.tools import ToolBinding, ToolExecutionPending
from saga_trpg.runtime.engine import DispatchOutcome, RuntimeEngine, validate_event_retry
from saga_trpg.runtime.hashing import stable_model_hash, stable_value_hash
from saga_trpg.runtime.journal import JournalEntry
from saga_trpg.runtime.replay import ReplayError, replay_recorded_results
from saga_trpg.runtime.rng import DeterministicDice

RUNTIME_VERSION = "0.2.0"


class RepositoryConflict(ValueError):
    """The requested world or branch already exists."""


class RepositoryNotFound(LookupError):
    """The requested world, branch, or fork event does not exist."""


@dataclass(frozen=True)
class EventLocation:
    """Where one branch holds an event; seq 0 denotes the initial world."""

    branch_id: str
    seq: int
    head_seq: int
    fork_seq: int
    pending_event_id: str | None


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
        external_dice: ToolBinding | None = None,
        seed: str = "dev-seed",
        snapshot_interval: int = 50,
    ) -> None:
        if snapshot_interval < 1:
            raise ValueError("snapshot_interval must be positive")
        if dice is not None and external_dice is not None:
            raise ValueError("choose local dice or a taped external tool binding")
        self.database = SQLiteDatabase(path)
        self.path = self.database.path
        self.rules = rules if rules is not None else GenericFantasyRules()
        self._explicit_rules = rules
        self.registry = RulesetRegistry()
        self.dice = dice
        self.external_dice = external_dice
        self.tool_tape = SQLiteToolResultTape(self.path)
        self.seed = seed
        self.snapshot_interval = snapshot_interval

    def _transaction(self, *, write: bool = False) -> AbstractContextManager[sqlite3.Connection]:
        return self.database.transaction(write=write)

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
        self,
        world_id: str,
        event: GameEvent,
        *,
        branch_id: str = "main",
        expected_seq: int | None = None,
        unique_in_world: bool = False,
    ) -> DispatchOutcome:
        """Commit or replay one event.

        The optional preconditions apply only to a new commit and are checked in its write
        transaction: the branch head must still be `expected_seq`, and with `unique_in_world`
        no other branch of the world may already hold the event id.
        """
        event = GameEvent.model_validate(event.model_dump())
        while True:
            outcome, required = self._dispatch_attempt(
                world_id,
                event,
                branch_id=branch_id,
                expected_seq=expected_seq,
                unique_in_world=unique_in_world,
            )
            if required is None:
                assert outcome is not None
                return outcome
            assert self.external_dice is not None
            # No world transaction is held across external execution. The committed branch
            # reservation prevents other actions from changing this event's input state.
            self.tool_tape.execute(required.call, self.external_dice.executor)

    def _dispatch_attempt(
        self,
        world_id: str,
        event: GameEvent,
        *,
        branch_id: str,
        expected_seq: int | None,
        unique_in_world: bool,
    ) -> tuple[DispatchOutcome | None, ToolResultRequired | None]:
        outcome = None
        required = None
        with self._transaction(write=True) as connection:
            state = self._load_state(connection, world_id, branch_id)
            world = self._world(connection, world_id)
            journal = SQLiteJournal(connection, world_id, branch_id)
            existing = (
                event.event_id is not None and journal.find_by_event_id(event.event_id) is not None
            ) or (
                event.idempotency_key is not None
                and journal.find_by_idempotency_key(event.idempotency_key) is not None
            )
            pending = connection.execute(
                """SELECT * FROM tool_events WHERE world_id = ? AND branch_id = ?
                   AND status = 'pending'""",
                (world_id, branch_id),
            ).fetchone()
            if not existing:
                if expected_seq is not None and state.seq != expected_seq:
                    raise RepositoryConflict("branch head changed; retry the request")
                if (
                    unique_in_world
                    and event.event_id is not None
                    and connection.execute(
                        "SELECT 1 FROM entries WHERE world_id = ? AND event_id = ?",
                        (world_id, event.event_id),
                    ).fetchone()
                ):
                    raise RepositoryConflict("event is already committed on another branch")
                if pending is not None:
                    saved = GameEvent.model_validate_json(pending["event_json"])
                    if not (
                        (event.event_id is not None and event.event_id == saved.event_id)
                        or (
                            event.idempotency_key is not None
                            and event.idempotency_key == saved.idempotency_key
                        )
                    ):
                        raise RepositoryConflict("branch has an unresolved external tool event")
                    validate_event_retry(event, saved)
                    if stable_model_hash(state) != pending["state_hash_before"]:
                        raise ReplayError("reserved tool event input state changed")
                    if self.external_dice is None:
                        raise ToolExecutionPending(
                            "reserved event requires its external tool binding"
                        )
                    event = saved
                if self.external_dice is not None and not (event.event_id or event.idempotency_key):
                    raise ValueError("external tools require an event_id or idempotency_key")
            dice: DicePort = (
                self.dice if self.dice is not None else DeterministicDice(world["seed"])
            )
            if self.external_dice is not None:
                dice = RecordedDice(
                    connection,
                    branch_id=branch_id,
                    binding=self.external_dice,
                    context_hash=stable_value_hash(
                        {
                            "event": event.model_dump(exclude={"event_id", "idempotency_key"}),
                            "state": stable_model_hash(state),
                            "runtime": RUNTIME_VERSION,
                            "ruleset": world["ruleset"],
                            "ruleset_version": world["ruleset_version"],
                        }
                    ),
                )
            engine = RuntimeEngine(
                initial_state=state,
                rules=self._select_rules(world["ruleset"], world["ruleset_version"]),
                dice=dice,
                journal=journal,
                event_id_prefix=world_id
                if branch_id == "main"
                else f"{world_id}:branch:{branch_id}",
            )
            try:
                outcome = engine.dispatch(event)
            except ToolResultRequired as exc:
                required = exc
                if pending is None:
                    reserved = event.model_copy(update={"event_id": exc.call.event_id})
                    connection.execute(
                        "INSERT INTO tool_events VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                        (
                            world_id,
                            branch_id,
                            reserved.event_id,
                            reserved.idempotency_key,
                            reserved.model_dump_json(),
                            stable_model_hash(state),
                        ),
                    )
            if outcome is not None and not outcome.replayed:
                if engine.state.seq % self.snapshot_interval == 0:
                    self._snapshot(connection, branch_id, engine.state)
                scope = (world_id, branch_id, outcome.entry.committed.event.event_id)
                connection.execute(
                    """UPDATE tool_events SET status = 'committed'
                       WHERE world_id = ? AND branch_id = ? AND event_id = ?""",
                    scope,
                )
                if isinstance(dice, RecordedDice):
                    for key in sorted(dice.consumed_keys):
                        connection.execute(
                            """UPDATE tool_executions SET status = 'committed'
                               WHERE world_id = ? AND branch_id = ? AND event_id = ?
                               AND tool_call_key = ? AND status = 'completed'""",
                            (*scope, key),
                        )
        return outcome, required

    def get_event(
        self, world_id: str, event_id: str, *, branch_id: str = "main"
    ) -> JournalEntry | None:
        with self._transaction() as connection:
            self._world(connection, world_id)
            self._branch(connection, world_id, branch_id)
            return SQLiteJournal(connection, world_id, branch_id).find_by_event_id(event_id)

    def entries(
        self, world_id: str, *, branch_id: str = "main", through_seq: int | None = None
    ) -> list[JournalEntry]:
        with self._transaction() as connection:
            self._world(connection, world_id)
            self._branch(connection, world_id, branch_id)
            return SQLiteJournal(connection, world_id, branch_id).entries(through_seq=through_seq)

    def locate_event(self, world_id: str, event_id: str | None) -> list[EventLocation]:
        """Branches whose history contains the event; None selects genesis in every branch."""
        source, parameters = "0", [world_id]
        if event_id is not None:
            source = """(SELECT seq FROM entries e WHERE e.world_id = b.world_id
                         AND e.branch_id = b.branch_id AND e.event_id = ?)"""
            parameters.insert(0, event_id)
        with self._transaction() as connection:
            self._world(connection, world_id)
            rows = connection.execute(
                f"""SELECT * FROM (SELECT b.branch_id, b.fork_seq, {source} AS seq,
                       COALESCE((SELECT MAX(seq) FROM entries h WHERE h.world_id = b.world_id
                                 AND h.branch_id = b.branch_id), 0) AS head_seq,
                       (SELECT event_id FROM tool_events t WHERE t.world_id = b.world_id
                        AND t.branch_id = b.branch_id AND t.status = 'pending')
                           AS pending_event_id
                       FROM branches b WHERE b.world_id = ?)
                    WHERE seq IS NOT NULL ORDER BY branch_id""",
                parameters,
            )
            return [EventLocation(**dict(row)) for row in rows]

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
