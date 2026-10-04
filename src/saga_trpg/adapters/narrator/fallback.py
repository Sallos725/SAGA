from __future__ import annotations

from saga_trpg.ports.narrator import NarrationRequest


class FallbackNarrator:
    async def render(self, request: NarrationRequest) -> str:
        if request.result.facts:
            return " ".join(request.result.facts)
        return f"Event {request.event.kind} resolved without a narrative line."
