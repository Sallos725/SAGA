import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from saga_trpg.adapters.storage.schema import BASE_SCHEMA, STORAGE_VERSION, TOOL_SCHEMA


class RepositoryVersionError(ValueError):
    """Stored data requires a different runtime or an explicit migration."""


class RepositoryConflict(ValueError):
    """The requested world, branch or rulebook already exists with different content."""


class RepositoryNotFound(LookupError):
    """The requested world, branch, or fork event does not exist."""


class SQLiteDatabase:
    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            raise ValueError("a persistent database file is required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction(write=True, initialize=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise RepositoryVersionError("unversioned database requires explicit migration")
                for statement in (*BASE_SCHEMA, *TOOL_SCHEMA):
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {STORAGE_VERSION}")
            elif version != STORAGE_VERSION:
                raise RepositoryVersionError(
                    f"unsupported storage version: {version}; explicit migration required"
                )

    @contextmanager
    def transaction(
        self, *, write: bool = False, initialize: bool = False
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
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
