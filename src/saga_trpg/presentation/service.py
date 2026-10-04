from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Literal

from saga_trpg.ports.performance import PerformanceConsumerPort
from saga_trpg.presentation.models import NarrativeSource, PerformanceEvent
from saga_trpg.presentation.segmentation import MetadataStatus, segment_narrative

logger = logging.getLogger(__name__)
DeliveryStatus = Literal["disabled", "delivered", "failed", "timeout"]


@dataclass(frozen=True)
class PresentationResult:
    text: str
    events: tuple[PerformanceEvent, ...] = ()
    metadata_status: MetadataStatus | Literal["disabled"] = "disabled"
    delivery_status: DeliveryStatus = "disabled"


async def present_narrative(
    text: str,
    *,
    source: NarrativeSource,
    actor_ids: frozenset[str],
    annotations_json: str | None = None,
    enabled: bool = False,
    consumer: PerformanceConsumerPort | None = None,
    timeout_seconds: float = 0.25,
) -> PresentationResult:
    """Optional post-text hook. Display text before awaiting this helper.

    The timeout is cooperative, not a remote cancellation/exactly-once guarantee. No retry
    is attempted, including after partially completed delivery. Caller cancellation escapes.
    """
    if not enabled:
        return PresentationResult(text=text)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("performance timeout must be finite and positive")
    segmentation = segment_narrative(
        text, source=source, actor_ids=actor_ids, annotations_json=annotations_json
    )
    status: DeliveryStatus = "disabled"
    if consumer is not None and segmentation.events:
        try:
            async with asyncio.timeout(timeout_seconds):
                await consumer.consume(segmentation.events)
            status = "delivered"
        except TimeoutError:
            status = "timeout"
            logger.warning("performance consumer timed out")
        except Exception:
            status = "failed"
            # Provider exceptions can include private dialogue, URLs and credentials.
            logger.warning("performance consumer failed")
    return PresentationResult(
        text=text,
        events=segmentation.events,
        metadata_status=segmentation.metadata_status,
        delivery_status=status,
    )
