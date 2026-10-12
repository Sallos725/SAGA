from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _absent(value: Any) -> bool:
    return value is None


def _empty(value: Any) -> bool:
    return not value


# Fields added after storage v2 are excluded from dumps while unset or empty, so a state saved
# before them keeps its exact JSON and hash (ADR 0009). Exclusion is by value, not
# exclude_unset, because rules build states with model_copy(update=...).


class ActorState(BaseModel):
    actor_id: str
    hp: int = 10
    max_hp: int = 10
    inventory: dict[str, int] = Field(default_factory=dict)
    conditions: set[str] = Field(default_factory=set)
    stats: dict[str, int | float | str | bool] = Field(default_factory=dict)
    location: str | None = Field(default=None, min_length=1, exclude_if=_absent)
    visited_rooms: set[str] = Field(default_factory=set, exclude_if=_empty)

    @field_serializer("conditions", "visited_rooms")
    def _sorted_sets(self, values: set[str]) -> list[str]:
        # Set iteration order depends on the hash seed; responses and stored JSON must not.
        return sorted(values)


class Exit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_room: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["open", "door"] = "open"


class Room(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    exits: dict[str, Exit] = Field(default_factory=dict)


class MapGenerator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    params: dict[str, int | str] = Field(default_factory=dict)


class MapState(BaseModel):
    """A room graph. Exits are directed and keyed per room (ADR 0009)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    start_room: str = Field(min_length=1)
    rooms: dict[str, Room] = Field(min_length=1)
    generator: MapGenerator | None = None


class WorldState(BaseModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    seq: int = Field(default=0, ge=0)
    simulated_time: int = 0
    actors: dict[str, ActorState] = Field(default_factory=dict)
    flags: dict[str, Any] = Field(default_factory=dict)
    quest_state: dict[str, Any] = Field(default_factory=dict)
    map: MapState | None = Field(default=None, exclude_if=_absent)


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
