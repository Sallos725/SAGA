from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from saga_trpg.presentation.models import (
    DialogueMetadata,
    DialogueSpan,
    NarrativeSource,
    PerformanceEvent,
    SpeakerRef,
)

MetadataStatus = Literal["absent", "accepted", "invalid"]
MAX_METADATA_CHARACTERS = 65_536


@dataclass(frozen=True)
class SegmentationResult:
    events: tuple[PerformanceEvent, ...]
    metadata_status: MetadataStatus


def segment_narrative(
    text: str,
    *,
    source: NarrativeSource,
    actor_ids: frozenset[str],
    annotations_json: str | None = None,
) -> SegmentationResult:
    """Project completed text; the caller supplies visible canonical ids from the commit.

    Unknown speakers stay unresolved. Reject malformed metadata as a whole so no text is
    dropped or duplicated by partially applying overlapping/out-of-order annotations.
    """
    spans: tuple[DialogueSpan, ...] = ()
    status: MetadataStatus = "absent"
    if annotations_json is not None:
        try:
            if len(annotations_json) > MAX_METADATA_CHARACTERS:
                raise ValueError("dialogue metadata exceeds the character limit")
            metadata = DialogueMetadata.model_validate_json(annotations_json)
            previous_end = 0
            for span in metadata.spans:
                if span.start < previous_end or span.end > len(text):
                    raise ValueError("dialogue spans must be ordered, disjoint and within text")
                previous_end = span.end
            spans = metadata.spans
            status = "accepted"
        except ValueError:
            status = "invalid"

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    events: list[PerformanceEvent] = []

    def append(
        start: int, end: int, kind: Literal["text", "dialogue"], speaker: SpeakerRef
    ) -> None:
        if start == end:
            return
        payload = {
            "schema_version": 1,
            "producer_version": "segments-v1",
            "source": source.model_dump(mode="json"),
            "text_hash": text_hash,
            "segment_index": len(events),
            "start": start,
            "end": end,
            "kind": kind,
            "text": text[start:end],
            "speaker": speaker.model_dump(mode="json"),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        event_id = "performance:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        events.append(PerformanceEvent(event_id=event_id, **payload))

    cursor = 0
    for span in spans:
        append(cursor, span.start, "text", SpeakerRef())
        speaker = span.speaker
        if speaker.kind == "actor" and speaker.actor_id not in actor_ids:
            speaker = SpeakerRef()
        append(span.start, span.end, "dialogue", speaker)
        cursor = span.end
    append(cursor, len(text), "text", SpeakerRef())
    return SegmentationResult(events=tuple(events), metadata_status=status)
