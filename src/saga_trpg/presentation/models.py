from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PresentationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def validate_version_type(cls, value: object) -> int:
        # Integer Literal validation otherwise accepts True and 1.0 as version 1.
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


class NarrativeSource(PresentationModel):
    world_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    game_event_id: str = Field(min_length=1)
    narration_id: str = Field(min_length=1)


class SpeakerRef(PresentationModel):
    kind: Literal["actor", "narrator", "unresolved"] = "unresolved"
    actor_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if (self.kind == "actor") != (self.actor_id is not None):
            raise ValueError("only an actor speaker must have an actor_id")
        return self


class DialogueSpan(PresentationModel):
    """End-exclusive Unicode code point offsets in the original narrative string."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    speaker: SpeakerRef = Field(default_factory=SpeakerRef)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end <= self.start:
            raise ValueError("dialogue span must have positive length")
        return self


class DialogueMetadata(PresentationModel):
    schema_version: Literal[1] = 1
    spans: tuple[DialogueSpan, ...] = Field(max_length=1024)


class PerformanceEvent(PresentationModel):
    """Derived presentation only; never dispatch this as a GameEvent."""

    schema_version: Literal[1] = 1
    producer_version: Literal["segments-v1"] = "segments-v1"
    event_id: str = Field(pattern=r"^performance:[0-9a-f]{64}$")
    source: NarrativeSource
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    kind: Literal["text", "dialogue"]
    text: str = Field(min_length=1)
    speaker: SpeakerRef

    @model_validator(mode="after")
    def validate_text_range(self) -> Self:
        if self.end - self.start != len(self.text):
            raise ValueError("segment text must match its code point range")
        if self.kind == "text" and self.speaker.kind != "unresolved":
            raise ValueError("unclassified text cannot assert a speaker")
        return self
