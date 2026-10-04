import sqlite3
from pathlib import Path

import pytest

from saga_trpg.adapters.storage.migrations import migrate_storage
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.runtime.hashing import stable_model_hash

EXPECTED_HASH = "92279c697d3ab7dd2406363b3d998b15b85c7318bb7cbe2b55e2629b1abc9edc"


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript((Path(__file__).parent / "fixtures/storage-v1.sql").read_text())
    return path


def test_v1_migration_preserves_history_hashes_and_recoverable_backup(legacy, tmp_path):
    with pytest.raises(ValueError, match="migration"):
        SQLiteWorldRepository(legacy)
    backup = tmp_path / "original.sqlite3"
    result = migrate_storage(legacy, backup_path=backup)
    assert (result.from_version, result.to_version) == (1, 2)
    repo = SQLiteWorldRepository(legacy)
    for branch in ("main", "alternate"):
        assert stable_model_hash(repo.replay("legacy", branch_id=branch)) == EXPECTED_HASH
        assert repo.get_state("legacy", branch_id=branch) == repo.replay("legacy", branch_id=branch)
    with sqlite3.connect(backup) as old, sqlite3.connect(legacy) as new:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 1
        for table in ("worlds", "branches", "entries", "snapshots"):
            assert (
                old.execute(f"SELECT * FROM {table}").fetchall()
                == new.execute(f"SELECT * FROM {table}").fetchall()
            )


def test_current_is_noop_without_backup_or_rewrite(tmp_path):
    path = tmp_path / "current.sqlite3"
    SQLiteWorldRepository(path)
    before = path.read_bytes()
    backup = tmp_path / "unused.sqlite3"
    result = migrate_storage(path, backup_path=backup)
    assert (result.from_version, result.to_version) == (2, 2)
    assert path.read_bytes() == before
    assert not backup.exists()


@pytest.mark.parametrize("version", [0, 999])
def test_unknown_storage_version_fails_closed(legacy, tmp_path, version):
    with sqlite3.connect(legacy) as connection:
        connection.execute(f"PRAGMA user_version = {version}")
    before = legacy.read_bytes()
    with pytest.raises(ValueError, match="version"):
        migrate_storage(legacy, backup_path=tmp_path / "unused.sqlite3")
    assert legacy.read_bytes() == before


def test_failed_migration_rolls_back_ddl_and_version(legacy, tmp_path, monkeypatch):
    from saga_trpg.adapters.storage import migrations

    original = migrations.MIGRATIONS[1]
    monkeypatch.setitem(migrations.MIGRATIONS, 1, (*original, "INVALID SQL"))
    with pytest.raises(sqlite3.DatabaseError):
        migrate_storage(legacy, backup_path=tmp_path / "original.sqlite3")
    with sqlite3.connect(legacy) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'tool_%'").fetchall()
            == []
        )


def test_corrupt_replay_and_existing_backup_are_rejected(legacy, tmp_path):
    backup = tmp_path / "original.sqlite3"
    backup.write_bytes(b"do not overwrite")
    with pytest.raises(FileExistsError):
        migrate_storage(legacy, backup_path=backup)
    assert backup.read_bytes() == b"do not overwrite"
    with sqlite3.connect(legacy) as connection:
        connection.execute("UPDATE snapshots SET state_hash = 'corrupt'")
    with pytest.raises(ValueError):
        migrate_storage(legacy, backup_path=tmp_path / "bad-source.sqlite3")
    with sqlite3.connect(legacy) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_history_rewrite_is_rejected_even_when_state_hash_is_unchanged(
    legacy, tmp_path, monkeypatch
):
    from saga_trpg.adapters.storage import migrations

    monkeypatch.setitem(
        migrations.MIGRATIONS,
        1,
        (
            *migrations.MIGRATIONS[1],
            "UPDATE entries SET entry_json = "
            "replace(entry_json, 'generic-fantasy.attack', 'different')",
        ),
    )
    with pytest.raises(ValueError, match="original data or history"):
        migrate_storage(legacy, backup_path=tmp_path / "original.sqlite3")
    with sqlite3.connect(legacy) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            "generic-fantasy.attack"
            in connection.execute("SELECT entry_json FROM entries LIMIT 1").fetchone()[0]
        )


def test_migration_cli_rejects_future_version_with_nonzero_exit(legacy, tmp_path):
    import subprocess
    import sys

    with sqlite3.connect(legacy) as connection:
        connection.execute("PRAGMA user_version = 999")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "saga_trpg.adapters.storage.migrations",
            str(legacy),
            "--backup",
            str(tmp_path / "unused.sqlite3"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unsupported storage version: 999" in result.stderr
    assert not (tmp_path / "unused.sqlite3").exists()
