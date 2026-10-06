from __future__ import annotations

from saga_trpg.ports.memory import MemoryHit, MemoryWrite


class NullMemory:
    async def remember(self, item: MemoryWrite) -> None:
        return None

    async def retrieve(
        self,
        *,
        world_id: str,
        query: str,
        actor_scope: str | None = None,
        limit: int = 8,
        branch_id: str = "main",
    ) -> list[MemoryHit]:
        return []
