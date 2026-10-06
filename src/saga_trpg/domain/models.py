from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer


class ActorState(BaseModel):
    actor_id: str
    hp: int = 10
    max_hp: int = 10
    inventory: dict[str, int] = Field(default_factory=dict)
    conditions: set[str] = Field(default_factory=set)
    stats: dict[str, int | float | str | bool] = Field(default_factory=dict)

    @field_serializer("conditions")
    def _sorted_conditions(self, conditions: set[str]) -> list[str]:
        # Set iteration order depends on the hash seed; responses and stored JSON must not.
        return sorted(conditions)


class WorldState(BaseModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    seq: int = Field(default=0, ge=0)
    simulated_time: int = 0
    actors: dict[str, ActorState] = Field(default_factory=dict)
    flags: dict[str, Any] = Field(default_factory=dict)
    quest_state: dict[str, Any] = Field(default_factory=dict)


class RollRecord(BaseModel):
    roll_key: str
    expression: str
    raw: list[int]
    modifier: int = 0
    total: int


class StatePatch(BaseModel):
    path: str
    before: Any = None
    after: Any = None


class RuleResolution(BaseModel):
    """Structured mechanical result; unresolved fictional choices are explicitly marked."""

    outcome: Literal[
        "failure", "tie", "success", "success_with_style", "miss", "hit", "critical_hit"
    ]
    total: int
    target: int
    damage: int | None = Field(default=None, ge=0)
    pending_adjudication: bool = False


class RuleResult(BaseModel):
    state: WorldState
    facts: list[str] = Field(default_factory=list)
    rolls: list[RollRecord] = Field(default_factory=list)
    patches: list[StatePatch] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    resolution: RuleResolution | None = None
