"""Explicit, offline storage upgrade. Never rewrite journal/state JSON or their hashes."""

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from saga_trpg.adapters.storage.database import RepositoryVersionError
from saga_trpg.adapters.storage.schema import STORAGE_VERSION, TOOL_SCHEMA
from saga_trpg.domain.models import WorldState
from saga_trpg.modules.catalog import RulesetRegistry
from saga_trpg.runtime.hashing import stable_model_hash, stable_value_hash
from saga_trpg.runtime.replay import ReplayError, replay_recorded_results

MIGRATIONS: dict[int, tuple[str, ...]] = {1: TOOL_SCHEMA}


@dataclass(frozen=True)
class MigrationResult:
    from_version: int
    to_version: int
    backup_path: Path | None


def migration_path(version: int) -> tuple[int, ...]:
    """Pure ordered plan; missing and future versions fail closed."""
    if version < 1 or version > STORAGE_VERSION:
        raise RepositoryVersionError(f"unsupported storage version: {version}")
    steps = tuple(range(version, STORAGE_VERSION))
    if any(step not in MIGRATIONS for step in steps):
        raise RepositoryVersionError(f"no migration from storage version: {version}")
    return steps


def _verify_history(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    from saga_trpg.adapters.storage.sqlite import RUNTIME_VERSION, SQLiteJournal

    if connection.execute("PRAGMA integrity_check").fetchall()[0][0] != "ok":
        raise ReplayError("database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ReplayError("database foreign key check failed")
    registry = RulesetRegistry()
    hashes = {}
    for world in connection.execute("SELECT * FROM worlds"):
        if world["runtime_version"] != RUNTIME_VERSION:
            raise RepositoryVersionError("incompatible runtime version; history cannot be migrated")
        registry.get(world["ruleset"], world["ruleset_version"])
        initial = WorldState.model_validate_json(world["initial_state_json"])
        if initial.world_id != world["world_id"] or initial.seq != 0:
            raise ReplayError("invalid initial state")
        for branch in connection.execute(
            "SELECT branch_id FROM branches WHERE world_id = ?", (world["world_id"],)
        ):
            scope = (world["world_id"], branch["branch_id"])
            entries = SQLiteJournal(connection, *scope).entries()
            final = replay_recorded_results(initial, entries)
            states = {0: initial, **{entry.committed.seq: entry.result.state for entry in entries}}
            snapshots = connection.execute(
                "SELECT * FROM snapshots WHERE world_id = ? AND branch_id = ?",
                scope,
            ).fetchall()
            if not snapshots:
                raise ReplayError("missing snapshots")
            for snapshot in snapshots:
                saved = WorldState.model_validate_json(snapshot["state_json"])
                if (
                    saved != states.get(snapshot["seq"])
                    or stable_model_hash(saved) != snapshot["state_hash"]
                ):
                    raise ReplayError("snapshot differs from replay")
            hashes[scope] = stable_model_hash(final)
    return hashes


def _original_data_hash(connection: sqlite3.Connection) -> str:
    order = {
        "worlds": "world_id",
        "branches": "world_id, branch_id",
        "entries": "world_id, branch_id, seq",
        "snapshots": "world_id, branch_id, seq",
    }
    return stable_value_hash(
        {
            table: [
                list(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {keys}")
            ]
            for table, keys in order.items()
        }
    )


def migrate_storage(path: str | Path, *, backup_path: str | Path) -> MigrationResult:
    """Stop writers, back up v1, then atomically add v2 tables and validate replay."""
    path, backup_path = Path(path).resolve(), Path(backup_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        steps = migration_path(version)
        if not steps:
            connection.rollback()
            return MigrationResult(version, version, None)
        # Reserve a new artifact, never overwrite a previous recovery copy. A separate read
        # connection can back up while this connection's RESERVED lock excludes writers.
        with backup_path.open("xb"):
            pass
        source = sqlite3.connect(path)
        backup = sqlite3.connect(backup_path)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        before = _verify_history(connection)
        original_data = _original_data_hash(connection)
        for step in steps:
            for statement in MIGRATIONS[step]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {step + 1}")
        if _verify_history(connection) != before:
            raise ReplayError("migration changed authoritative replay hashes")
        if _original_data_hash(connection) != original_data:
            raise ReplayError("storage-only migration changed original data or history")
        connection.commit()
        return MigrationResult(version, STORAGE_VERSION, backup_path)
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    args = parser.parse_args()
    result = migrate_storage(args.database, backup_path=args.backup)
    print(f"Storage {result.from_version} -> {result.to_version}; backup: {result.backup_path}")


if __name__ == "__main__":
    main()
