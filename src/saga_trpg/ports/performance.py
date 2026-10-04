from typing import Protocol

from saga_trpg.presentation.models import PerformanceEvent


class PerformanceConsumerPort(Protocol):
    async def consume(self, events: tuple[PerformanceEvent, ...]) -> None:
        """Consume derived events, deduplicating effects by event_id.

        Delivery may repeat. Implementations must cooperate with cancellation and avoid
        blocking the event loop. No game state, repository, memory or tool port is provided.
        """
        ...
