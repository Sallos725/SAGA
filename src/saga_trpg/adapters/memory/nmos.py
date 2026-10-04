from __future__ import annotations

from typing import Any

from saga_trpg.ports.memory import MemoryHit, MemoryWrite


class NMOSAdapter:
    """NMOS integration boundary.

    Do not assume NMOS internals or vendor NMOS code here. During implementation, inspect the
    current NMOS public/local API and bind only the operations required by MemoryPort.

    Recommended scoping model:
      world:<world_id>/global
      world:<world_id>/actor:<actor_id>/firsthand
      world:<world_id>/actor:<actor_id>/heard

    Exact naming should follow NMOS's real namespace/filter capabilities at implementation time.
    """

    def __init__(self, *, base_url: str, api_key: str | None = None, client: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client

    async def remember(self, item: MemoryWrite) -> None:
        raise NotImplementedError(
            "Inspect current NMOS API and map MemoryWrite "
            "to a durable NMOS memory/fact/event write."
        )

    async def retrieve(
        self,
        *,
        world_id: str,
        query: str,
        actor_scope: str | None = None,
        limit: int = 8,
    ) -> list[MemoryHit]:
        raise NotImplementedError(
            "Inspect current NMOS retrieval API; preserve world/actor visibility filters."
        )
