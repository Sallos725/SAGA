from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GameEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str | None = Field(default=None, min_length=1)
    kind: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    parent_event_id: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)


class CommittedEvent(BaseModel):
    event: GameEvent
    seq: int
    state_hash_before: str
    state_hash_after: str
