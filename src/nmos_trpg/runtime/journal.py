from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from nmos_trpg.domain.events import CommittedEvent
from nmos_trpg.domain.models import RuleResult


class JournalEntry(BaseModel):
    schema_version: Literal[1] = 1
    runtime_version: Literal["0.2.0"] = "0.2.0"
    committed: CommittedEvent
    result: RuleResult


class JournalPort(Protocol):
    def append(self, entry: JournalEntry) -> None: ...

    def entries(self) -> list[JournalEntry]: ...

    def find_by_event_id(self, event_id: str) -> JournalEntry | None: ...

    def find_by_idempotency_key(self, key: str) -> JournalEntry | None: ...


class InMemoryJournal:
    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []

    def _validate_identity(self, entry: JournalEntry) -> None:
        if not entry.committed.event.event_id:
            raise ValueError("a committed event requires an event id")
        if self.find_by_event_id(entry.committed.event.event_id or ""):
            raise ValueError(f"duplicate event id: {entry.committed.event.event_id}")
        key = entry.committed.event.idempotency_key
        if key is not None and self.find_by_idempotency_key(key):
            raise ValueError(f"duplicate idempotency key: {key}")

    def append(self, entry: JournalEntry) -> None:
        self._validate_identity(entry)
        self._entries.append(entry.model_copy(deep=True))

    def entries(self) -> list[JournalEntry]:
        return [entry.model_copy(deep=True) for entry in self._entries]

    def find_by_event_id(self, event_id: str) -> JournalEntry | None:
        for entry in self._entries:
            if entry.committed.event.event_id == event_id:
                return entry.model_copy(deep=True)
        return None

    def find_by_idempotency_key(self, key: str) -> JournalEntry | None:
        for entry in self._entries:
            if entry.committed.event.idempotency_key == key:
                return entry.model_copy(deep=True)
        return None


class JsonlJournal(InMemoryJournal):
    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    super().append(JournalEntry.model_validate_json(line))

    def append(self, entry: JournalEntry) -> None:
        self._validate_identity(entry)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.model_dump(mode="json"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._entries.append(entry.model_copy(deep=True))
