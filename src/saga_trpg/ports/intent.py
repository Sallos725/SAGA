from __future__ import annotations

from typing import Protocol

from saga_trpg.domain.events import GameEvent


class IntentPort(Protocol):
    async def interpret(self, *, world_id: str, actor_id: str, text: str) -> GameEvent:
        """Translate free text into a proposed, schema-valid event.

        Implementations may use an LLM, but must choose from an allowed event schema and must
        not mutate state.
        """
        ...
