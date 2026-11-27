"""Scenario card initial-world contract, separate from rulebook mechanics (M2).

A scenario defines *what exists* at campaign start (actors, starting map/placements, flags,
quest_state, setting narration), while a rulebook defines *how mechanics resolve*. Creators
can pair any scenario with any compatible rulebook without editing rulebook files.

Everything in this module is pure and deterministic: no network I/O, no wall clock."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from saga_trpg.domain.mapgen import MAX_ROOMS as MAX_GEN_ROOMS
from saga_trpg.domain.mapgen import MIN_ROOMS as MIN_GEN_ROOMS
from saga_trpg.domain.mapgen import generate_map
from saga_trpg.domain.maps import map_problems
from saga_trpg.domain.models import ActorState, MapState, WorldState
from saga_trpg.rulebooks.loader import (
    ITEM_KEY,
    MAX_AUTHORED_ROOMS,
    VERSION,
    Closed,
    ExampleActor,
    ExampleMap,
    RulebookSpec,
)
from saga_trpg.rulebooks.risu_module import sha256_text

SCENARIO_ID_PATTERN = r"^[a-z0-9][a-z0-9.-]{0,60}$"


class ScenarioError(ValueError):
    """A scenario document failed schema validation or compatibility checks."""


class ScenarioSpec(Closed):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=SCENARIO_ID_PATTERN)
    version: str = Field(pattern=VERSION)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=2000)
    ruleset_id: str | None = Field(default=None, pattern=SCENARIO_ID_PATTERN)
    simulated_time: int = Field(default=0, ge=0)
    actors: dict[str, ExampleActor] = Field(min_length=1)
    flags: dict[str, Any] = Field(default_factory=dict)
    quest_state: dict[str, Any] = Field(default_factory=dict)
    map: ExampleMap | None = None
    narration: str = Field(default="", max_length=8000)


def parse_scenario(text: str) -> ScenarioSpec:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"invalid scenario JSON: {exc.msg}") from None
    try:
        spec = ScenarioSpec.model_validate(raw)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in err['loc']) or 'scenario'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise ScenarioError("; ".join(problems)) from None
    problems = scenario_problems(spec)
    if problems:
        raise ScenarioError("; ".join(problems))
    return spec


def load_scenario(path: Path | str) -> ScenarioSpec:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError(f"cannot read scenario {path}: {exc}") from None
    return parse_scenario(text)


def _check_map(example_map: ExampleMap, actor_ids: set[str], location: str = "map") -> list[str]:
    problems: list[str] = []
    if example_map.generate is not None:
        if example_map.start_room or example_map.rooms or example_map.placements:
            problems.append(f"{location}: choose either generate or hand-authored start_room/rooms")
        low, high = example_map.generate.rooms
        if not (MIN_GEN_ROOMS <= low <= high <= MAX_GEN_ROOMS):
            problems.append(
                f"{location}.generate.rooms: bounds must satisfy "
                f"{MIN_GEN_ROOMS} <= min <= max <= {MAX_GEN_ROOMS}"
            )
        return problems
    if not example_map.start_room or not example_map.rooms:
        problems.append(f"{location}: provide start_room and rooms, or generate")
        return problems
    if len(example_map.rooms) > MAX_AUTHORED_ROOMS:
        problems.append(f"{location}.rooms: at most {MAX_AUTHORED_ROOMS} rooms allowed")
        return problems
    try:
        built = MapState(start_room=example_map.start_room, rooms=example_map.rooms)
    except ValidationError as exc:
        problems.append(f"{location}: {exc.errors()[0]['msg']}")
        return problems
    problems.extend(map_problems(built, location=location))
    for actor_id, room_id in example_map.placements.items():
        if actor_id not in actor_ids:
            problems.append(f"{location}.placements.{actor_id}: unknown actor")
        if room_id not in example_map.rooms:
            problems.append(f"{location}.placements.{actor_id}: unknown room {room_id!r}")
    return problems


def scenario_problems(spec: ScenarioSpec, rulebook: RulebookSpec | None = None) -> list[str]:
    problems: list[str] = []
    if "player" not in spec.actors:
        problems.append("actors: scenario must define a 'player' actor")
    if rulebook is not None and spec.ruleset_id is not None and spec.ruleset_id != rulebook.id:
        problems.append(f"ruleset_id: scenario requires {spec.ruleset_id!r}, got {rulebook.id!r}")
    import re

    for actor_id, actor in spec.actors.items():
        if actor.max_hp < 1:
            problems.append(f"actors.{actor_id}.max_hp: must be >= 1")
        if not (0 <= actor.hp <= actor.max_hp):
            problems.append(f"actors.{actor_id}.hp: must be within 0..{actor.max_hp}")
        for item_key, count in actor.inventory.items():
            if not re.fullmatch(ITEM_KEY, item_key):
                problems.append(f"actors.{actor_id}.inventory.{item_key}: invalid item key")
            if count < 0:
                problems.append(f"actors.{actor_id}.inventory.{item_key}: count must be >= 0")
            if rulebook is not None and rulebook.items and item_key not in rulebook.items:
                problems.append(
                    f"actors.{actor_id}.inventory.{item_key}: unknown item for {rulebook.id}"
                )
        if rulebook is not None:
            for condition in actor.conditions:
                if condition not in rulebook.conditions:
                    problems.append(
                        f"actors.{actor_id}.conditions: unknown condition {condition!r} "
                        f"for {rulebook.id}"
                    )
    if spec.map is not None:
        problems.extend(_check_map(spec.map, set(spec.actors)))
    return problems


def build_scenario_world(
    scenario: ScenarioSpec,
    world_id: str,
    *,
    rulebook: RulebookSpec | None = None,
) -> WorldState:
    problems = scenario_problems(scenario, rulebook)
    if problems:
        raise ScenarioError("; ".join(problems))

    game_map: MapState | None = None
    placements: dict[str, str] = {}
    if scenario.map is not None:
        if scenario.map.generate is not None:
            low, high = scenario.map.generate.rooms
            game_map = generate_map(
                f"{scenario.id}:{scenario.version}:{scenario.map.generate.seed}:{world_id}",
                min_rooms=low,
                max_rooms=high,
            )
        else:
            assert scenario.map.start_room is not None
            game_map = MapState(
                start_room=scenario.map.start_room,
                rooms=deepcopy(scenario.map.rooms),
            )
            placements = dict(scenario.map.placements)

    actors: dict[str, ActorState] = {}
    for actor_id, item in scenario.actors.items():
        location: str | None = None
        visited: set[str] = set()
        if game_map is not None:
            if actor_id in placements:
                location = placements[actor_id]
            elif actor_id == "player":
                location = game_map.start_room
            if location is not None:
                visited = {location}
        actors[actor_id] = ActorState(
            actor_id=actor_id,
            hp=item.hp,
            max_hp=item.max_hp,
            stats=dict(item.stats),
            inventory={k: v for k, v in item.inventory.items() if v > 0},
            conditions=set(item.conditions),
            location=location,
            visited_rooms=visited,
        )

    flags = deepcopy(scenario.flags)
    flags.setdefault("_scenario", {"id": scenario.id, "version": scenario.version})
    return WorldState(
        world_id=world_id,
        simulated_time=scenario.simulated_time,
        actors=actors,
        flags=flags,
        quest_state=deepcopy(scenario.quest_state),
        map=game_map,
    )


def build_scenario_module(scenario_text: str) -> dict[str, Any]:
    """Wrap a validated scenario JSON into a plain RisuAI module (`type: "risuModule"`)."""
    spec = parse_scenario(scenario_text)
    digest = sha256_text(scenario_text)
    lorebook: list[dict[str, Any]] = []
    if spec.narration.strip() or spec.summary.strip():
        parts = [f"# Scenario: {spec.title} ({spec.id}@{spec.version})"]
        if spec.summary.strip():
            parts.append(spec.summary.strip())
        if spec.narration.strip():
            parts.append(spec.narration.strip())
        lorebook.append(
            {
                "key": "",
                "secondkey": "",
                "insertorder": 95,
                "comment": f"saga:scenario-narration:{spec.id}@{spec.version}",
                "content": "\n\n".join(parts),
                "mode": "constant",
                "alwaysActive": True,
                "selective": False,
                "useRegex": False,
                "activationPercent": 100,
                "bookVersion": 2,
            }
        )
    lorebook.append(
        {
            "key": "(?!)",
            "secondkey": "",
            "insertorder": 0,
            "comment": f"saga:scenario:{spec.id}@{spec.version}:{digest}",
            "content": scenario_text,
            "mode": "normal",
            "alwaysActive": False,
            "selective": False,
            "useRegex": True,
            "activationPercent": 100,
            "bookVersion": 2,
        }
    )
    return {
        "type": "risuModule",
        "module": {
            "id": f"saga-scenario-{spec.id}-{spec.version}",
            "name": f"SAGA Scenario — {spec.title}",
            "description": spec.summary or f"SAGA scenario {spec.id}@{spec.version}",
            "lorebook": lorebook,
        },
    }
