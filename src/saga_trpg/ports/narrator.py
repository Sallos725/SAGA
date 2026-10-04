from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import RuleResult
from saga_trpg.ports.memory import MemoryHit


class NarrationRequest(BaseModel):
    world_id: str
    event: GameEvent
    result: RuleResult
    memories: list[MemoryHit] = Field(default_factory=list)
    recent_dialogue: list[str] = Field(default_factory=list)


class NarratorPort(Protocol):
    async def render(self, request: NarrationRequest) -> str: ...
