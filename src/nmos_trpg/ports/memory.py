from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class MemoryWrite(BaseModel):
    world_id: str
    actor_scope: str | None = None
    event_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryHit(BaseModel):
    text: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryPort(Protocol):
    async def remember(self, item: MemoryWrite) -> None: ...

    async def retrieve(
        self,
        *,
        world_id: str,
        query: str,
        actor_scope: str | None = None,
        limit: int = 8,
    ) -> list[MemoryHit]: ...
