from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_serializer,
    model_serializer,
)


class ActorState(BaseModel):
    actor_id: str
    hp: int = 10
    max_hp: int = 10
    inventory: dict[str, int] = Field(default_factory=dict)
    conditions: set[str] = Field(default_factory=set)
    stats: dict[str, int | float | str | bool] = Field(default_factory=dict)
    location: str | None = None
    visited_rooms: set[str] = Field(default_factory=set)

    @field_serializer("conditions", "visited_rooms")
    def _sorted_sets(self, values: set[str]) -> list[str]:
        # Set iteration order depends on the hash seed; responses and stored JSON must not.
        return sorted(values)

    @model_serializer(mode="wrap")
    def _omit_unplaced(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Fields added after storage v2 disappear while empty, so actors saved before them keep
        # their exact JSON and state hash (ADR 0009).
        data = handler(self)
        if data.get("location") is None:
            data.pop("location", None)
        if not data.get("visited_rooms"):
            data.pop("visited_rooms", None)
        return data


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
    map: MapState | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_map(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # A world without a map dumps, stores and hashes exactly as before ADR 0009.
        data = handler(self)
        if data.get("map") is None:
            data.pop("map", None)
        return data


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
