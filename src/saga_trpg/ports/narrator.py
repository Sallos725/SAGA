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
    # Optional host-assembled prompt messages (presets, persona, lorebook) preserved at the
    # front of OpenAI-compatible narrator calls; never mutates state.
    risu_prompt: list[dict[str, str]] = Field(default_factory=list)
    # The player's known map (ADR 0009). Narrators get this projection, never the full map.
    scene: str | None = None


class NarratorPort(Protocol):
    async def render(self, request: NarrationRequest) -> str: ...
