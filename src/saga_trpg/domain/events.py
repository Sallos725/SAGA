from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NARRATIVE_TURN_KIND = "narrative_turn"
ADJUDICATE_ACTION_KIND = "adjudicate_action"


class NarrativeTurnPayload(BaseModel):
    """Closed empty payload for a state-neutral narrative turn (ADR 0010)."""

    model_config = ConfigDict(extra="forbid")


class AdjudicateActionPayload(BaseModel):
    """Closed payload for a GM-adjudicated free action (A1 1st slice, ADR 0011).

    The GM/intent boundary selects mode, difficulty, modifier, and stakes *before* rolling;
    RuntimeEngine rolls deterministically through DicePort and records an immutable journal
    entry so regeneration can never alter difficulty or re-roll outcomes."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=240)
    mode: Literal["auto_success", "check", "impossible"]
    difficulty: int | None = Field(default=None, ge=1, le=100)
    modifier: int = Field(default=0, ge=-20, le=20)
    advantage: bool = False
    disadvantage: bool = False
    dice_expression: Literal["1d20", "2d6", "4d3-8"] = "1d20"
    ability: str | None = Field(default=None, min_length=1, max_length=40)
    stakes: str = Field(default="", max_length=240)
    reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def validate_mode_fields(self) -> AdjudicateActionPayload:
        if self.mode == "check" and self.difficulty is None:
            raise ValueError("difficulty is required when mode is 'check'")
        if self.mode != "check" and self.difficulty is not None:
            raise ValueError("difficulty is only allowed when mode is 'check'")
        if self.mode != "check" and (self.advantage or self.disadvantage):
            raise ValueError("advantage/disadvantage are only allowed when mode is 'check'")
        if self.dice_expression != "1d20" and (self.advantage or self.disadvantage):
            raise ValueError("advantage/disadvantage are only supported for 1d20")
        return self


class GameEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str | None = Field(default=None, min_length=1)
    kind: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    parent_event_id: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)


class CommittedEvent(BaseModel):
    event: GameEvent
    seq: int
    state_hash_before: str
    state_hash_after: str
