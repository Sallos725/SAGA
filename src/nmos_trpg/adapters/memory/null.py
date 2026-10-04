from __future__ import annotations

from nmos_trpg.ports.memory import MemoryHit, MemoryWrite


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
    ) -> list[MemoryHit]:
        return []
