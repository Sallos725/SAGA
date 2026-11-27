from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NARRATIVE_TURN_KIND = "narrative_turn"
ADJUDICATE_ACTION_KIND = "adjudicate_action"
UNLOCK_EXIT_KIND = "unlock_exit"
TAKE_LOOT_KIND = "take_loot"
INSPECT_ROOM_KIND = "inspect_room"
MOVE_NPC_KIND = "move_npc"
QUEST_UPDATE_KIND = "quest_update"
GM_CORRECTION_KIND = "gm_correction"

_IDENTIFIER = r"^[a-z0-9][a-z0-9_-]{0,59}$"


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


class UnlockExitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]{0,39}$")


class TakeLootPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]{0,59}$")
    amount: int = Field(default=1, ge=1, le=999)


class InspectRoomPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clue_id: str | None = Field(default=None, min_length=1, max_length=60, pattern=_IDENTIFIER)


class MoveNpcPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1, max_length=60)
    exit: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]{0,39}$")


class QuestUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quest_id: str = Field(min_length=1, max_length=60, pattern=_IDENTIFIER)
    stage: int | None = Field(default=None, ge=0, le=999)
    status: Literal["active", "completed", "failed"] | None = None
    objective: str | None = Field(default=None, min_length=1, max_length=240)
    note: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def require_some_change(self) -> QuestUpdatePayload:
        if all(v is None for v in (self.stage, self.status, self.objective, self.note)):
            raise ValueError("quest_update requires at least one field to update")
        return self


class GmCorrectionPayload(BaseModel):
    """Explicit append-only GM correction / scene settlement (A3, ADR 0012).

    Never edits prior journal entries in place; records exact reason, optional target event
    provenance, and validated state deltas as a new committed event."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=240)
    target_event_id: str | None = Field(default=None, min_length=1, max_length=200)
    hp_changes: dict[str, int] = Field(default_factory=dict)
    inventory_changes: dict[str, dict[str, int]] = Field(default_factory=dict)
    add_conditions: dict[str, list[str]] = Field(default_factory=dict)
    remove_conditions: dict[str, list[str]] = Field(default_factory=dict)
    quest_updates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    flags_set: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_some_correction(self) -> GmCorrectionPayload:
        if not any(
            (
                self.hp_changes,
                self.inventory_changes,
                self.add_conditions,
                self.remove_conditions,
                self.quest_updates,
                self.flags_set,
            )
        ):
            raise ValueError("gm_correction requires at least one state change")
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
