# Locations and map movement (ADR 0009) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Worlds get a room-graph map in authoritative state, players move with `move <exit>`, and the narrator and footer see only what the player knows.

**Architecture:** Optional `WorldState.map` plus per-actor `location`/`visited_rooms`, omitted from serialization while empty so existing worlds keep their exact hash. A pure seeded generator and a pure projection live in `domain/`; the rulebook engine gains the `map_move` builtin and builds maps in `example_world`; new bundled versions `srd-5.2.1@0.6.0` and `fate-condensed@0.5.0` opt in. Spec: [ADR 0009](../../adr/0009-locations-and-map-movement.md).

**Tech Stack:** Python 3.11–3.13, pydantic 2, pytest, ruff. No new dependencies.

---

## Ground rules for this plan

- Work in the worktree root (`.claude/worktrees/risuai-mcp-map-generation-cfaca0`) on branch
  `claude/risuai-mcp-map-generation-cfaca0`.
- Use the main checkout's virtualenv; `pyproject.toml` puts this worktree's `src` first on the
  path, so tests always run this tree's code:

  ```bash
  export PY=/home/grantkim725/data/docker/risuaiNode/risu-trpg/.venv/bin/python
  export RUFF=/home/grantkim725/data/docker/risuaiNode/risu-trpg/.venv/bin/ruff
  ```
- Baseline before Task 1: `$PY -m pytest -q` → `548 passed`.
- Every code block below was run in a prototype of this exact change: the full suite ended at
  `597 passed`, `ruff check` and `ruff format --check` clean. Copy blocks as written.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hard rules from `AGENTS.md` apply: no I/O, clock or unseeded randomness in `domain/`; the LLM
  never mutates state; rerender reuses journaled results.

## File map

| File | Change | Responsibility |
| --- | --- | --- |
| `src/saga_trpg/domain/models.py` | modify | `Exit`, `Room`, `MapGenerator`, `MapState`; `WorldState.map`; `ActorState.location`/`visited_rooms`; hash-neutral serializers |
| `src/saga_trpg/domain/maps.py` | create | validation (`map_problems`, `reachable`), projection (`known_map`), text (`render_known_map`, `scene_text`) |
| `src/saga_trpg/domain/mapgen.py` | create | seeded generator `generate_map`, room table |
| `src/saga_trpg/rulebooks/loader.py` | modify | `map_move` builtin, `ExampleMap`/`MapGenerate`, map checks, `location` display entry |
| `src/saga_trpg/rulebooks/engine.py` | modify | payload model for `map_move`, maps in `example_world`, `_move` |
| `src/saga_trpg/rulebooks/commands.py` | modify | `move <exit>` positional order |
| `src/saga_trpg/rulebooks/display.py` | modify | `📍 room · 출구 keys` world line |
| `src/saga_trpg/rulebooks/bundled/srd-5.2.1@0.6.0.json` | create | SRD with `move` and a generated example map |
| `src/saga_trpg/rulebooks/bundled/fate-condensed@0.5.0.json` | create | Fate with `move` and a hand-authored map |
| `src/saga_trpg/ports/narrator.py` | modify | `NarrationRequest.scene` |
| `src/saga_trpg/adapters/narrator/vertex.py` | modify | drop `map` from committed JSON, add the scene part |
| `src/saga_trpg/adapters/risu/openai_proxy.py` | modify | pass `scene_text(...)` to the narrator |
| `tests/test_state_compat.py` | create | golden hash and JSON for map-less worlds |
| `tests/test_maps.py` | create | validation, projection, generator |
| `tests/test_map_rulebooks.py` | create | loader, worlds, `map_move`, footer, bundled versions |
| `tests/test_risu_api.py`, `tests/test_vertex_narration.py` | modify | bridge and narrator behaviour; version pins |
| `tests/test_rulebooks.py`, `tests/test_rulebooks_fate.py`, `tests/test_rulebook_display.py`, `tests/test_risu_module.py` | modify | version pins only |
| `schemas/world.schema.json` | regenerate | new optional fields |
| `docs/RULEBOOKS.md`, `docs/adr/0009-…`, `docs/progress/locations-map-v1.md`, `docs/progress/README.md` | modify/create | author docs, ADR accepted, progress report |

---

### Task 1: Pin the state hash of a map-less world (characterization test)

This test passes on today's code and must keep passing after every later task. It is the
guard for ADR 0009's hash-neutrality rule: if it ever fails, stop and fix the serializer, not
the constants.

**Files:**
- Create: `tests/test_state_compat.py`

- [ ] **Step 1: Write the test**

```python
"""Worlds saved before a state field existed keep their exact JSON and hash (ADR 0009)."""

import hashlib

from saga_trpg.domain.models import WorldState
from saga_trpg.modules.catalog import RulesetRegistry
from saga_trpg.runtime.hashing import stable_model_hash

# Captured from the code before ADR 0009 added map, location and visited_rooms.
GOLDEN_STATE_HASH = "44e533cdb4aaf225605dc1b90294b78bc759a2a5514686b3557513d95150834c"
GOLDEN_JSON_SHA256 = "e634ccfeeeda2736206a050b104acfb6128b19f00c97c4029c607f356749b5ca"


def golden_state() -> WorldState:
    state = RulesetRegistry().get("srd-5.2.1", "0.5.0").example_world("golden")
    state = state.model_copy(
        update={"seq": 3, "simulated_time": 12, "flags": {"aspects": {"e1": {"name": "Fog"}}}}
    )
    state.actors["opponent"].conditions = {"prone", "blinded"}
    state.actors["player"].inventory = {"rope": 1}
    return state


def test_a_mapless_world_keeps_its_state_hash_and_stored_json():
    state = golden_state()
    assert stable_model_hash(state) == GOLDEN_STATE_HASH
    assert hashlib.sha256(state.model_dump_json().encode()).hexdigest() == GOLDEN_JSON_SHA256


def test_a_mapless_world_round_trips_through_stored_json():
    state = golden_state()
    again = WorldState.model_validate_json(state.model_dump_json())
    assert again == state
    assert stable_model_hash(again) == GOLDEN_STATE_HASH
```

- [ ] **Step 2: Run it on the unchanged code**

Run: `$PY -m pytest tests/test_state_compat.py -v`
Expected: `2 passed`. (If it fails here, the constants were not captured from this revision;
do not continue.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_state_compat.py
git commit -m "test: pin state hash and stored JSON of a map-less world"
```

---

### Task 2: Map state models with hash-neutral serialization

**Files:**
- Modify: `src/saga_trpg/domain/models.py`
- Test: `tests/test_state_compat.py`

- [ ] **Step 1: Write the failing tests**

Change the models import at the top of `tests/test_state_compat.py` to:

```python
from saga_trpg.domain.models import ActorState, MapState, Room, WorldState
```

and append:

```python
def test_unplaced_actor_fields_are_omitted_from_both_dumps():
    actor = ActorState(actor_id="a")
    assert "location" not in actor.model_dump()
    assert "visited_rooms" not in actor.model_dump()
    assert "location" not in actor.model_dump_json()
    assert "visited_rooms" not in actor.model_dump_json()


def test_a_map_and_placement_are_serialized_sorted_and_round_trip():
    state = golden_state()
    state.map = MapState(start_room="hall", rooms={"hall": Room(name="입구 홀")})
    state.actors["player"].location = "hall"
    state.actors["player"].visited_rooms = {"hall", "cellar"}
    dumped = state.model_dump(mode="json")
    assert dumped["map"]["start_room"] == "hall"
    assert dumped["actors"]["player"]["visited_rooms"] == ["cellar", "hall"]
    assert "location" not in dumped["actors"]["opponent"]
    assert WorldState.model_validate_json(state.model_dump_json()) == state
    assert stable_model_hash(state) != GOLDEN_STATE_HASH
```

- [ ] **Step 2: Run to see them fail**

Run: `$PY -m pytest tests/test_state_compat.py -v`
Expected: collection error, `ImportError: cannot import name 'MapState'`.

- [ ] **Step 3: Implement**

Apply this change to `src/saga_trpg/domain/models.py`. The wrap serializers drop the new fields
by value (not `exclude_unset`), because rules build states with `model_copy(update=...)`.

```diff
@@ -2,7 +2,14 @@
 
 from typing import Any, Literal
 
-from pydantic import BaseModel, Field, field_serializer
+from pydantic import (
+    BaseModel,
+    ConfigDict,
+    Field,
+    SerializerFunctionWrapHandler,
+    field_serializer,
+    model_serializer,
+)
 
 
 class ActorState(BaseModel):
@@ -12,11 +19,59 @@
     inventory: dict[str, int] = Field(default_factory=dict)
     conditions: set[str] = Field(default_factory=set)
     stats: dict[str, int | float | str | bool] = Field(default_factory=dict)
+    location: str | None = None
+    visited_rooms: set[str] = Field(default_factory=set)
 
-    @field_serializer("conditions")
-    def _sorted_conditions(self, conditions: set[str]) -> list[str]:
+    @field_serializer("conditions", "visited_rooms")
+    def _sorted_sets(self, values: set[str]) -> list[str]:
         # Set iteration order depends on the hash seed; responses and stored JSON must not.
-        return sorted(conditions)
+        return sorted(values)
+
+    @model_serializer(mode="wrap")
+    def _omit_unplaced(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
+        # Fields added after storage v2 disappear while empty, so actors saved before them keep
+        # their exact JSON and state hash (ADR 0009).
+        data = handler(self)
+        if data.get("location") is None:
+            data.pop("location", None)
+        if not data.get("visited_rooms"):
+            data.pop("visited_rooms", None)
+        return data
+
+
+class Exit(BaseModel):
+    model_config = ConfigDict(extra="forbid")
+
+    to_room: str = Field(min_length=1)
+    label: str = Field(min_length=1, max_length=80)
+    kind: Literal["open", "door"] = "open"
+
+
+class Room(BaseModel):
+    model_config = ConfigDict(extra="forbid")
+
+    name: str = Field(min_length=1, max_length=80)
+    description: str = Field(default="", max_length=2000)
+    exits: dict[str, Exit] = Field(default_factory=dict)
+
+
+class MapGenerator(BaseModel):
+    model_config = ConfigDict(extra="forbid")
+
+    name: str = Field(min_length=1)
+    version: str = Field(min_length=1)
+    params: dict[str, int | str] = Field(default_factory=dict)
+
+
+class MapState(BaseModel):
+    """A room graph. Exits are directed and keyed per room (ADR 0009)."""
+
+    model_config = ConfigDict(extra="forbid")
+
+    schema_version: Literal[1] = 1
+    start_room: str = Field(min_length=1)
+    rooms: dict[str, Room] = Field(min_length=1)
+    generator: MapGenerator | None = None
 
 
 class WorldState(BaseModel):
@@ -27,6 +82,15 @@
     actors: dict[str, ActorState] = Field(default_factory=dict)
     flags: dict[str, Any] = Field(default_factory=dict)
     quest_state: dict[str, Any] = Field(default_factory=dict)
+    map: MapState | None = None
+
+    @model_serializer(mode="wrap")
+    def _omit_absent_map(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
+        # A world without a map dumps, stores and hashes exactly as before ADR 0009.
+        data = handler(self)
+        if data.get("map") is None:
+            data.pop("map", None)
+        return data
 
 
 class RollRecord(BaseModel):
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `$PY -m pytest tests/test_state_compat.py -v` → `4 passed`.
Run: `$PY -m pytest -q` → `552 passed` (548 + 4). `tests/test_migrations.py` replays the
storage-v1 fixture and verifies snapshot hashes, so a passing suite also proves stored worlds
still verify.

- [ ] **Step 5: Commit**

```bash
git add src/saga_trpg/domain/models.py tests/test_state_compat.py
git commit -m "feat: optional map and actor location in state, omitted while empty"
```

---

### Task 3: Map validation and the known-map projection

**Files:**
- Create: `src/saga_trpg/domain/maps.py`
- Create: `tests/test_maps.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Room-graph validation, the known-map projection and the seeded generator (ADR 0009)."""

import pytest

from saga_trpg.domain.maps import (
    known_map,
    map_problems,
    reachable,
    render_known_map,
    scene_text,
)
from saga_trpg.domain.models import ActorState, Exit, MapState, Room, WorldState

def three_rooms() -> MapState:
    return MapState(
        start_room="hall",
        rooms={
            "hall": Room(
                name="입구 홀",
                description="먼지 쌓인 홀.",
                exits={
                    "north": Exit(to_room="guard", label="북쪽 문", kind="door"),
                    "east": Exit(to_room="store", label="동쪽 통로", kind="open"),
                },
            ),
            "guard": Room(
                name="경비실", exits={"south": Exit(to_room="hall", label="남쪽 문", kind="door")}
            ),
            "store": Room(
                name="창고", exits={"west": Exit(to_room="hall", label="서쪽 통로", kind="open")}
            ),
        },
    )


def world(location="hall", visited=("hall",)) -> WorldState:
    return WorldState(
        world_id="w",
        map=three_rooms(),
        actors={
            "player": ActorState(actor_id="player", location=location, visited_rooms=set(visited)),
            "wolf": ActorState(actor_id="wolf", location="guard", visited_rooms={"guard"}),
        },
    )


# --- validation ---------------------------------------------------------------------------------


def test_a_connected_map_has_no_problems():
    assert map_problems(three_rooms()) == []
    assert reachable(three_rooms()) == {"hall", "guard", "store"}


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda m: setattr(m, "start_room", "attic"), "map.start_room: unknown room 'attic'"),
        (
            lambda m: m.rooms["hall"].exits.update(up=Exit(to_room="attic", label="위")),
            "map.rooms.hall.exits.up.to_room: unknown room 'attic'",
        ),
        (
            lambda m: m.rooms["hall"].exits.update(Up=Exit(to_room="guard", label="위")),
            "map.rooms.hall.exits.Up: exit keys are lower-case identifiers",
        ),
        (lambda m: m.rooms.update(Attic=Room(name="다락")), "map.rooms.Attic: room ids"),
        (
            lambda m: m.rooms.update(attic=Room(name="다락")),
            "map: rooms unreachable from hall: attic",
        ),
    ],
)
def test_map_problems_name_the_entry(change, message):
    game_map = three_rooms()
    change(game_map)
    assert any(problem.startswith(message) for problem in map_problems(game_map)), map_problems(
        game_map
    )



# --- projection ---------------------------------------------------------------------------------


def test_known_map_hides_rooms_behind_unvisited_doors():
    view = known_map(world(), "player")
    assert view.room_name == "입구 홀" and view.occupants == []
    exits = {exit_.key: exit_ for exit_ in view.exits}
    assert exits["north"].destination == "???"  # a door, never visited
    assert exits["east"].destination == "창고"  # an open passage shows the next room
    assert view.visited == ["입구 홀"]
    text = render_known_map(view)
    assert "경비실" not in text and "wolf" not in text


def test_visiting_a_room_reveals_it_and_its_occupants():
    view = known_map(world(location="guard", visited=("hall", "guard")), "player")
    assert view.room_name == "경비실" and view.occupants == ["wolf"]
    assert [(e.key, e.destination, e.visited) for e in view.exits] == [("south", "입구 홀", True)]
    assert view.visited == ["경비실", "입구 홀"]


def test_rendered_block_lists_current_room_exits_and_visited_rooms():
    assert render_known_map(known_map(world(), "player")) == "\n".join(
        [
            "현재 위치: 입구 홀 — 먼지 쌓인 홀.",
            "출구:",
            "- east: 동쪽 통로 → 창고",
            "- north: 북쪽 문 → ???",
            "가본 곳: 입구 홀",
        ]
    )


def test_no_map_or_no_location_means_no_scene():
    assert scene_text(WorldState(world_id="w"), "player") is None
    unplaced = world()
    unplaced.actors["player"].location = None
    assert scene_text(unplaced, "player") is None
    assert scene_text(world(), "nobody") is None
```

- [ ] **Step 2: Run to see them fail**

Run: `$PY -m pytest tests/test_maps.py -v`
Expected: `ModuleNotFoundError: No module named 'saga_trpg.domain.maps'`.

- [ ] **Step 3: Implement `src/saga_trpg/domain/maps.py`**

```python
"""Room-graph helpers: validation, the player's known map and its text form (ADR 0009).

Everything here is pure. The known map is a projection of committed state; it is recomputed
on every read and never stored, so rerendering a reply cannot change it.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from saga_trpg.domain.models import MapState, WorldState

EXIT_KEY = r"^[a-z][a-z0-9_]{0,39}$"
ROOM_ID = r"^[a-z0-9][a-z0-9_-]{0,39}$"
UNKNOWN = "???"


def reachable(game_map: MapState) -> set[str]:
    seen = {game_map.start_room}
    queue = [game_map.start_room]
    while queue:
        room = game_map.rooms.get(queue.pop())
        if room is None:
            continue
        for exit_ in room.exits.values():
            if exit_.to_room not in seen:
                seen.add(exit_.to_room)
                queue.append(exit_.to_room)
    return seen


def map_problems(game_map: MapState, location: str = "map") -> list[str]:
    """Plain-language problems by entry path; an empty list means the map is usable."""
    problems = []
    if game_map.start_room not in game_map.rooms:
        problems.append(f"{location}.start_room: unknown room {game_map.start_room!r}")
    for room_id, room in game_map.rooms.items():
        if not re.fullmatch(ROOM_ID, room_id):
            problems.append(f"{location}.rooms.{room_id}: room ids are lower-case identifiers")
        for key, exit_ in room.exits.items():
            where = f"{location}.rooms.{room_id}.exits.{key}"
            if not re.fullmatch(EXIT_KEY, key):
                problems.append(
                    f"{where}: exit keys are lower-case identifiers such as north or cellar_stairs"
                )
            if exit_.to_room not in game_map.rooms:
                problems.append(f"{where}.to_room: unknown room {exit_.to_room!r}")
    if not problems:
        unreachable = sorted(set(game_map.rooms) - reachable(game_map))
        if unreachable:
            problems.append(
                f"{location}: rooms unreachable from {game_map.start_room}: "
                + ", ".join(unreachable)
            )
    return problems


class KnownExit(BaseModel):
    key: str
    label: str
    kind: Literal["open", "door"]
    destination: str
    visited: bool


class KnownMap(BaseModel):
    room_id: str
    room_name: str
    description: str
    occupants: list[str]
    exits: list[KnownExit]
    visited: list[str]


def known_map(state: WorldState, actor_id: str) -> KnownMap | None:
    """What `actor_id` knows: the current room in full, exits with destinations only where
    visited or seen through an open passage, and the names of visited rooms."""
    actor = state.actors.get(actor_id)
    if state.map is None or actor is None or actor.location is None:
        return None
    rooms = state.map.rooms
    room = rooms[actor.location]
    exits = []
    for key in sorted(room.exits):
        exit_ = room.exits[key]
        visited = exit_.to_room in actor.visited_rooms
        destination = rooms[exit_.to_room].name if visited or exit_.kind == "open" else UNKNOWN
        exits.append(
            KnownExit(
                key=key,
                label=exit_.label,
                kind=exit_.kind,
                destination=destination,
                visited=visited,
            )
        )
    occupants = sorted(
        other_id
        for other_id, other in state.actors.items()
        if other_id != actor_id and other.location == actor.location
    )
    visited_names = [rooms[room_id].name for room_id in sorted(actor.visited_rooms)]
    return KnownMap(
        room_id=actor.location,
        room_name=room.name,
        description=room.description,
        occupants=occupants,
        exits=exits,
        visited=visited_names,
    )


def scene_text(state: WorldState, actor_id: str) -> str | None:
    """The map block given to the narrator: the actor's known map, or None without a map."""
    view = known_map(state, actor_id)
    return render_known_map(view) if view is not None else None


def render_known_map(view: KnownMap) -> str:
    head = f"현재 위치: {view.room_name}"
    lines = [f"{head} — {view.description}" if view.description else head]
    if view.occupants:
        lines.append("여기 있는 것: " + ", ".join(view.occupants))
    lines.append("출구:")
    for exit_ in view.exits:
        note = " (가봄)" if exit_.visited else ""
        lines.append(f"- {exit_.key}: {exit_.label} → {exit_.destination}{note}")
    if not view.exits:
        lines.append("- 없음")
    lines.append("가본 곳: " + ", ".join(view.visited))
    return "\n".join(lines)
```

- [ ] **Step 4: Run**

Run: `$PY -m pytest tests/test_maps.py -v` → `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/saga_trpg/domain/maps.py tests/test_maps.py
git commit -m "feat: map validation and the player's known-map projection"
```

---

### Task 4: Seeded map generator

**Files:**
- Create: `src/saga_trpg/domain/mapgen.py`
- Modify: `tests/test_maps.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_maps.py` add `import hashlib` above `import pytest`, add this import after the
`pytest` import (ruff's isort order puts it before `saga_trpg.domain.maps`):

```python
from saga_trpg.domain.mapgen import MAX_ROOMS, ROOMS, generate_map
```

add this constant directly after the imports:

```python
# generate_map("srd-demo|golden", min_rooms=6, max_rooms=10), GENERATOR_VERSION "1".
GOLDEN_MAP_SHA256 = "ce760c11178c7e15e5b17c18f5df0f939485157f8afba030dd367e51d7dfbff6"
```

and append:

```python
# --- generator ----------------------------------------------------------------------------------


def test_the_room_table_covers_the_largest_map_without_repeats():
    assert len(ROOMS) >= MAX_ROOMS
    assert len({name for name, _ in ROOMS}) == len(ROOMS)


def test_same_seed_gives_the_same_map_and_a_pinned_digest():
    first = generate_map("srd-demo|golden", min_rooms=6, max_rooms=10)
    again = generate_map("srd-demo|golden", min_rooms=6, max_rooms=10)
    assert first == again
    assert hashlib.sha256(first.model_dump_json().encode()).hexdigest() == GOLDEN_MAP_SHA256
    assert first.start_room == "room_1" and first.rooms["room_1"].name == "입구 홀"
    assert first.generator.params == {"min_rooms": 6, "max_rooms": 10}


def test_different_worlds_get_different_maps():
    a = generate_map("srd-demo|chat-a", min_rooms=6, max_rooms=10)
    b = generate_map("srd-demo|chat-b", min_rooms=6, max_rooms=10)
    assert a != b


@pytest.mark.parametrize("bounds", [(2, 2), (2, 30), (30, 30), (6, 10)])
def test_generated_maps_are_connected_two_way_and_bounded(bounds):
    low, high = bounds
    for seed in range(200):
        game_map = generate_map(f"seed-{seed}", min_rooms=low, max_rooms=high)
        assert low <= len(game_map.rooms) <= high
        assert map_problems(game_map) == []
        names = [room.name for room in game_map.rooms.values()]
        assert len(set(names)) == len(names)
        for room_id, room in game_map.rooms.items():
            assert len(room.exits) <= 6
            for exit_ in room.exits.values():
                back = game_map.rooms[exit_.to_room].exits.values()
                assert any(other.to_room == room_id and other.kind == exit_.kind for other in back)


@pytest.mark.parametrize("bounds", [(1, 5), (5, 4), (2, 31)])
def test_out_of_range_room_counts_are_refused(bounds):
    with pytest.raises(ValueError, match="room count"):
        generate_map("x", min_rooms=bounds[0], max_rooms=bounds[1])
```

- [ ] **Step 2: Run to see them fail**

Run: `$PY -m pytest tests/test_maps.py -v`
Expected: `ModuleNotFoundError: No module named 'saga_trpg.domain.mapgen'`.

- [ ] **Step 3: Implement `src/saga_trpg/domain/mapgen.py`**

The digest in `GOLDEN_MAP_SHA256` depends on every line below (table order, draw order,
probabilities). If you change any of it, bump `GENERATOR_VERSION` and recapture the digest in
the same commit; stored worlds are unaffected because replay never reruns the generator.

```python
"""Seeded room-graph generator (ADR 0009).

Pure: the same seed material always gives the same map. A generated map is stored in the
world's initial state, so changing this module (and bumping GENERATOR_VERSION) never alters
an existing world; replay reads the stored map and does not call the generator.
"""

from __future__ import annotations

import hashlib
import random

from saga_trpg.domain.models import Exit, MapGenerator, MapState, Room

GENERATOR_NAME = "saga-rooms"
GENERATOR_VERSION = "1"
MIN_ROOMS = 2
MAX_ROOMS = 30

OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "up": "down",
    "down": "up",
}
DIRECTION_WORDS = {
    "north": "북쪽",
    "south": "남쪽",
    "east": "동쪽",
    "west": "서쪽",
    "up": "위쪽",
    "down": "아래쪽",
}
KIND_WORDS = {"open": "통로", "door": "문"}

# Room name and description pairs; the first is the start room. At least MAX_ROOMS entries,
# so names never repeat within a map.
ROOMS = (
    ("입구 홀", "먼지 쌓인 돌바닥에 오래된 발자국이 흐릿하게 남아 있다."),
    ("경비실", "낡은 무기 거치대가 벽을 따라 늘어선 좁은 방."),
    ("창고", "썩은 나무 상자와 찢어진 자루가 어지럽게 쌓여 있다."),
    ("지하 예배당", "금이 간 제단 위로 촛농이 굳어 흘러내려 있다."),
    ("무너진 서재", "책장 절반이 쓰러져 종잇조각이 바닥을 덮고 있다."),
    ("우물방", "방 한가운데 둥근 우물에서 차가운 바람이 올라온다."),
    ("감옥", "녹슨 쇠창살 너머로 빈 족쇄가 매달려 있다."),
    ("부엌", "그을린 화덕과 깨진 냄비가 남아 있다."),
    ("식당", "긴 탁자 위에 먼지 덮인 식기가 그대로 놓여 있다."),
    ("병영", "삐걱거리는 이층 침대가 줄지어 서 있다."),
    ("무기고", "빈 선반마다 칼집 자국만 남아 있다."),
    ("보물고", "두꺼운 철문 안쪽 벽에 빈 진열대가 박혀 있다."),
    ("연금술 작업실", "깨진 유리 기구에서 시큼한 냄새가 난다."),
    ("지하수로", "발목까지 차오른 물이 천천히 흐른다."),
    ("버섯 동굴", "희미하게 빛나는 버섯이 천장까지 자라 있다."),
    ("석상 회랑", "얼굴이 깎여 나간 석상들이 양쪽에 서 있다."),
    ("납골당", "벽감마다 이름 모를 유골함이 놓여 있다."),
    ("훈련장", "모래 바닥에 부서진 허수아비가 쓰러져 있다."),
    ("마구간", "오래된 건초 냄새가 아직 남아 있다."),
    ("탑 계단참", "좁은 창으로 바깥 빛이 가늘게 들어온다."),
    ("기도실", "작은 방석 세 개가 빈 제단을 향해 놓여 있다."),
    ("포도주 저장고", "깨진 술통에서 흘러나온 얼룩이 바닥에 말라붙어 있다."),
    ("하인 숙소", "얇은 매트리스와 작은 궤짝이 몇 개 남아 있다."),
    ("지도실", "벽에 바랜 지도가 핀으로 고정되어 있다."),
    ("거미굴", "구석마다 두꺼운 거미줄이 늘어져 있다."),
    ("붕괴한 통로 끝방", "천장 일부가 내려앉아 돌무더기가 쌓여 있다."),
    ("의식의 방", "바닥에 분필로 그린 원이 반쯤 지워져 있다."),
    ("수비대장실", "부서진 책상 위에 찢어진 명령서가 흩어져 있다."),
    ("환기 갱도", "벽 틈새로 바람이 휘파람 소리를 낸다."),
    ("지하 호수", "검은 물이 고요하게 고여 끝이 보이지 않는다."),
)


def generate_map(seed_material: str, *, min_rooms: int, max_rooms: int) -> MapState:
    if not MIN_ROOMS <= min_rooms <= max_rooms <= MAX_ROOMS:
        raise ValueError(f"room count needs {MIN_ROOMS} <= min <= max <= {MAX_ROOMS}")
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest, "big"))
    count = rng.randint(min_rooms, max_rooms)
    # The start room is always the entrance hall; the rest are drawn without repetition.
    picks = [0, *rng.sample(range(1, len(ROOMS)), count - 1)]
    ids = [f"room_{index + 1}" for index in range(count)]
    links: dict[str, dict[str, tuple[str, str]]] = {room_id: {} for room_id in ids}

    def free(a: str, b: str) -> list[str]:
        return [d for d in OPPOSITE if d not in links[a] and OPPOSITE[d] not in links[b]]

    def connect(a: str, b: str, direction: str) -> None:
        kind = "door" if rng.randrange(3) == 0 else "open"
        links[a][direction] = (b, kind)
        links[b][OPPOSITE[direction]] = (a, kind)

    # Spanning tree: every room joins an earlier one, so all are reachable from room_1.
    # A tree on k rooms has k-1 links, so some earlier room always has a free direction.
    for index in range(1, count):
        new = ids[index]
        parent = rng.choice([old for old in ids[:index] if free(old, new)])
        connect(parent, new, rng.choice(free(parent, new)))
    # A few extra links make loops; an attempt is skipped if the rooms are already linked
    # or no direction pair is free in both.
    for _ in range(count // 4):
        a, b = rng.sample(ids, 2)
        options = free(a, b)
        if options and all(to != b for to, _ in links[a].values()):
            connect(a, b, rng.choice(options))

    rooms = {}
    for room_id, pick in zip(ids, picks, strict=True):
        name, description = ROOMS[pick]
        rooms[room_id] = Room(
            name=name,
            description=description,
            exits={
                direction: Exit(
                    to_room=to,
                    label=f"{DIRECTION_WORDS[direction]} {KIND_WORDS[kind]}",
                    kind=kind,
                )
                for direction, (to, kind) in sorted(links[room_id].items())
            },
        )
    return MapState(
        start_room=ids[0],
        rooms=rooms,
        generator=MapGenerator(
            name=GENERATOR_NAME,
            version=GENERATOR_VERSION,
            params={"min_rooms": min_rooms, "max_rooms": max_rooms},
        ),
    )
```

- [ ] **Step 4: Run**

Run: `$PY -m pytest tests/test_maps.py -v` → `20 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/saga_trpg/domain/mapgen.py tests/test_maps.py
git commit -m "feat: seeded room-graph generator"
```

---

### Task 5: Maps in rulebooks — schema, checks and example worlds

**Files:**
- Modify: `src/saga_trpg/rulebooks/loader.py`
- Modify: `src/saga_trpg/rulebooks/engine.py` (imports and `example_world` only in this task)
- Create: `tests/test_map_rulebooks.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_map_rulebooks.py` with the full import header (later tasks append sections
that use the rest of it):

```python
"""Maps in rulebooks: example_world.map, the map_move builtin, commands and the footer."""

import json

import pytest

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.mapgen import generate_map
from saga_trpg.modules.catalog import FATE_LATEST, SRD_LATEST, RulesetRegistry
from saga_trpg.rulebooks.commands import CommandError, command_help, parse_command
from saga_trpg.rulebooks.display import render_display
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import RulebookError, load_rulebook, parse_rulebook, rulebook_path
from saga_trpg.runtime.engine import RuntimeEngine
from saga_trpg.runtime.journal import InMemoryJournal
from saga_trpg.runtime.replay import replay_recorded_results
from saga_trpg.runtime.rng import DeterministicDice

HAND_MAP = {
    "start_room": "hall",
    "rooms": {
        "hall": {
            "name": "입구 홀",
            "exits": {
                "north": {"to_room": "guard", "label": "북쪽 문", "kind": "door"},
                "east": {"to_room": "store", "label": "동쪽 통로"},
            },
        },
        "guard": {"name": "경비실", "exits": {"south": {"to_room": "hall", "label": "남쪽 문"}}},
        "store": {"name": "창고", "exits": {"west": {"to_room": "hall", "label": "서쪽 통로"}}},
    },
    "placements": {"opponent": "guard"},
}


def srd_with(world_map, version="0.5.0"):
    data = json.loads(rulebook_path("srd-5.2.1", version).read_text())
    data["actions"]["move"] = {"builtin": "map_move"}
    data["example_world"]["map"] = world_map
    return data


def engine(spec, world_id="w"):
    rules = DataRules(spec)
    return RuntimeEngine(
        initial_state=rules.example_world(world_id),
        rules=rules,
        dice=DeterministicDice(),
        journal=InMemoryJournal(),
    )


def move(spec, text, event_id):
    event = parse_command(spec, actor_id="player", text=text)
    return event.model_copy(update={"event_id": event_id})


# --- loader -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda m: m.update(start_room="attic"), "example_world.map.start_room: unknown room"),
        (lambda m: m.pop("start_room"), "example_world.map: a hand-authored map needs"),
        (
            lambda m: m["rooms"]["hall"]["exits"]["north"].update(to_room="attic"),
            "example_world.map.rooms.hall.exits.north.to_room: unknown room 'attic'",
        ),
        (
            lambda m: m["rooms"].update(attic={"name": "다락"}),
            "example_world.map: rooms unreachable from hall: attic",
        ),
        (
            lambda m: m["placements"].update(ghost="hall"),
            "example_world.map.placements.ghost: unknown actor",
        ),
        (
            lambda m: m["placements"].update(opponent="attic"),
            "example_world.map.placements.opponent: unknown room 'attic'",
        ),
        (
            lambda m: m.update(generate={"seed": "x"}),
            "example_world.map: give either generate or",
        ),
    ],
)
def test_invalid_maps_are_reported_by_entry_path(change, message):
    world_map = json.loads(json.dumps(HAND_MAP))
    change(world_map)
    with pytest.raises(RulebookError) as error:
        parse_rulebook(srd_with(world_map))
    assert message in str(error.value)


@pytest.mark.parametrize("rooms", [[1, 4], [8, 6], [2, 31]])
def test_generator_room_bounds_are_checked(rooms):
    with pytest.raises(RulebookError, match="example_world.map.generate.rooms"):
        parse_rulebook(srd_with({"generate": {"seed": "x", "rooms": rooms}}))



# --- worlds -------------------------------------------------------------------------------------


def test_a_hand_authored_map_places_actors_and_marks_start_rooms_visited():
    state = DataRules(parse_rulebook(srd_with(HAND_MAP))).example_world("w")
    assert state.map.start_room == "hall" and state.map.generator is None
    assert state.actors["player"].location == "hall"
    assert state.actors["player"].visited_rooms == {"hall"}
    assert state.actors["opponent"].location == "guard"


def test_a_generated_map_depends_on_seed_and_world_id():
    spec = parse_rulebook(srd_with({"generate": {"seed": "demo", "rooms": [6, 10]}}))
    rules = DataRules(spec)
    a = rules.example_world("chat-a")
    again = rules.example_world("chat-a")
    b = rules.example_world("chat-b")
    assert a.map == again.map == generate_map("demo|chat-a", min_rooms=6, max_rooms=10)
    assert a.map != b.map
    assert {actor.location for actor in a.actors.values()} == {a.map.start_room}


def test_worlds_without_a_map_are_unchanged():
    state = DataRules(load_rulebook(rulebook_path("srd-5.2.1", "0.5.0"))).example_world("w")
    assert state.map is None
    assert all(actor.location is None for actor in state.actors.values())
```

- [ ] **Step 2: Run to see them fail**

Run: `$PY -m pytest tests/test_map_rulebooks.py -v`
Expected: failures — `RulebookError` for unknown `example_world.map` / `map_move`.

- [ ] **Step 3: Implement the loader**

```diff
@@ -15,7 +15,9 @@
 
 from pydantic import BaseModel, ConfigDict, Field, ValidationError
 
-from saga_trpg.domain.models import RuleResolution
+from saga_trpg.domain.mapgen import MAX_ROOMS, MIN_ROOMS
+from saga_trpg.domain.maps import map_problems
+from saga_trpg.domain.models import MapState, Room, RuleResolution
 from saga_trpg.rulebooks.expr import ExpressionError, compile_expression
 
 KEY = r"^[a-z][a-z0-9_]{0,39}$"
@@ -34,6 +36,7 @@
     "inventory_remove",
     "record_adjust",
     "record_remove",
+    "map_move",
 ]
 
 
@@ -165,9 +168,24 @@
     conditions: list[str] = Field(default_factory=list)
 
 
+class MapGenerate(Closed):
+    seed: str = Field(min_length=1, max_length=80)
+    rooms: tuple[int, int] = (6, 10)
+
+
+class ExampleMap(Closed):
+    """Either a hand-authored room graph or a generator request (ADR 0009)."""
+
+    start_room: str | None = None
+    rooms: dict[str, Room] = Field(default_factory=dict)
+    placements: dict[str, str] = Field(default_factory=dict)
+    generate: MapGenerate | None = None
+
+
 class ExampleWorld(Closed):
     actors: dict[str, ExampleActor]
     flags: dict[str, Any] = Field(default_factory=dict)
+    map: ExampleMap | None = None
 
 
 class DisplaySpec(Closed):
@@ -207,6 +225,7 @@
     "time_advance": {"amount", "unit"},
     "inventory_add": {"target_id", "item", "amount"},
     "inventory_remove": {"target_id", "item", "amount"},
+    "map_move": {"exit"},
 }
 
 
@@ -479,7 +498,7 @@
     for section, allowed in (
         ("actor", builtin_actor | set(spec.stats)),
         ("records", set(spec.records)),
-        ("world", {"time"}),
+        ("world", {"time", "location"}),
     ):
         entries = getattr(spec.display, section)
         for entry in entries:
@@ -509,6 +528,34 @@
                     )
             if not 0 <= actor.hp <= actor.max_hp or actor.max_hp <= 0:
                 yield f"example_world.actors.{actor_id}: hp must be within 0..max_hp"
+        if spec.example_world.map is not None:
+            yield from _check_map(spec.example_world)
+
+
+def _check_map(world: ExampleWorld):
+    spec_map = world.map
+    location = "example_world.map"
+    if spec_map.generate is not None:
+        if spec_map.rooms or spec_map.start_room is not None or spec_map.placements:
+            yield f"{location}: give either generate or start_room/rooms/placements, not both"
+        low, high = spec_map.generate.rooms
+        if not MIN_ROOMS <= low <= high <= MAX_ROOMS:
+            yield (
+                f"{location}.generate.rooms: needs [min, max] with "
+                f"{MIN_ROOMS} <= min <= max <= {MAX_ROOMS}"
+            )
+        return
+    if spec_map.start_room is None or not spec_map.rooms:
+        yield f"{location}: a hand-authored map needs start_room and rooms"
+        return
+    yield from map_problems(
+        MapState(start_room=spec_map.start_room, rooms=spec_map.rooms), location
+    )
+    for actor_id, room_id in spec_map.placements.items():
+        if actor_id not in world.actors:
+            yield f"{location}.placements.{actor_id}: unknown actor"
+        elif room_id not in spec_map.rooms:
+            yield f"{location}.placements.{actor_id}: unknown room {room_id!r}"
 
 
 _RESERVED_NAMES = frozenset(
```

- [ ] **Step 4: Implement maps in `example_world`**

From the engine diff below apply only these hunks now: the imports, the `example_world`
change and the module-level `_example_map` helper (place it just above `_find_record`). The
`map_move` payload, the `_builtin` branch and `_move` come in Task 6.

```diff
@@ -9,8 +9,11 @@
 from pydantic import Field, create_model
 
 from saga_trpg.domain.events import GameEvent
+from saga_trpg.domain.mapgen import generate_map
+from saga_trpg.domain.maps import EXIT_KEY
 from saga_trpg.domain.models import (
     ActorState,
+    MapState,
     RuleResolution,
     RuleResult,
     StatePatch,
@@ -23,6 +26,7 @@
     ITEM_KEY,
     BuiltinAction,
     CheckAction,
+    ExampleMap,
     InputSpec,
     RecordField,
     RulebookSpec,
@@ -201,8 +205,10 @@
                 fields["disadvantage"] = (bool, False)
         else:
             builtin = action.builtin
-            if builtin != "time_advance":
+            if builtin not in ("time_advance", "map_move"):
                 fields["target_id"] = (str, Field(min_length=1))
+            if builtin == "map_move":
+                fields["exit"] = (str, Field(pattern=EXIT_KEY))
             if builtin == "hp_change" or builtin.startswith("inventory_"):
                 fields["amount"] = (int, Field(ge=1, le=1000))
             if builtin.startswith("condition_"):
@@ -252,7 +258,15 @@
                     conditions=set(actor.conditions),
                 )
         flags = dict(self.spec.example_world.flags) if self.spec.example_world else {}
-        return WorldState(world_id=world_id, actors=actors, flags=flags)
+        state = WorldState(world_id=world_id, actors=actors, flags=flags)
+        spec_map = self.spec.example_world.map if self.spec.example_world else None
+        if spec_map is not None:
+            state.map = _example_map(spec_map, world_id)
+            for actor_id, actor in state.actors.items():
+                room_id = spec_map.placements.get(actor_id, state.map.start_room)
+                actor.location = room_id
+                actor.visited_rooms = {room_id}
+        return state
 
     # -- dispatch ----------------------------------------------------------------------------
 
@@ -556,6 +570,8 @@
             )
         if action.builtin.startswith("record_"):
             return self._record(state, next_state, event, action, data)
+        if action.builtin == "map_move":
+            return self._move(state, next_state, event, data)
         target = actor_in(state, data["target_id"])
         self._hp_sane(target)
         changed = next_state.actors[target.actor_id]
@@ -613,6 +629,59 @@
             trace=self._trace(event, f"builtin={action.builtin}"),
         )
 
+    def _move(
+        self, state: WorldState, next_state: WorldState, event: GameEvent, data: dict[str, Any]
+    ) -> RuleResult:
+        # The exit key is resolved against the actor's room in this state, at apply time, so
+        # command parsing stays stateless and replay reads the recorded result (ADR 0009).
+        if state.map is None:
+            raise ValueError("this world has no map")
+        actor = actor_in(state, event.actor_id)
+        if actor.location is None:
+            raise ValueError(f"{actor.actor_id} is not on the map")
+        room = state.map.rooms[actor.location]
+        exit_ = room.exits.get(data["exit"])
+        if exit_ is None:
+            known = ", ".join(sorted(room.exits)) or "none"
+            raise ValueError(f"{room.name} has no exit {data['exit']!r}; exits: {known}")
+        moved = next_state.actors[actor.actor_id]
+        moved.location = exit_.to_room
+        moved.visited_rooms.add(exit_.to_room)
+        destination = state.map.rooms[exit_.to_room]
+        return RuleResult(
+            state=next_state,
+            patches=[
+                StatePatch(
+                    path=f"actors.{actor.actor_id}.location",
+                    before=actor.location,
+                    after=exit_.to_room,
+                ),
+                StatePatch(
+                    path=f"actors.{actor.actor_id}.visited_rooms",
+                    before=sorted(actor.visited_rooms),
+                    after=sorted(moved.visited_rooms),
+                ),
+            ],
+            facts=[
+                f"{actor.actor_id}: {event.kind} through {exit_.label} "
+                f"from {room.name} to {destination.name}."
+            ],
+            trace=self._trace(
+                event, f"builtin=map_move; exit={data['exit']}; {actor.location}->{exit_.to_room}"
+            ),
+        )
+
+
+def _example_map(spec_map: ExampleMap, world_id: str) -> MapState:
+    if spec_map.generate is not None:
+        low, high = spec_map.generate.rooms
+        # Each world (a RisuAI chat) gets its own dungeon; the same world always the same one.
+        return generate_map(f"{spec_map.generate.seed}|{world_id}", min_rooms=low, max_rooms=high)
+    return MapState(
+        start_room=spec_map.start_room,
+        rooms={room_id: room.model_copy(deep=True) for room_id, room in spec_map.rooms.items()},
+    )
+
 
 def _find_record(state: WorldState, collection: str, field: str, value: Any) -> str | None:
     stored = state.flags.get(collection, {})
```

- [ ] **Step 5: Run**

Run: `$PY -m pytest tests/test_map_rulebooks.py -v` → `13 passed`.
Run: `$PY -m pytest -q` → no failures.

- [ ] **Step 6: Commit**

```bash
git add src/saga_trpg/rulebooks/loader.py src/saga_trpg/rulebooks/engine.py tests/test_map_rulebooks.py
git commit -m "feat: example_world.map in rulebooks, hand-authored or generated per world"
```

---

### Task 6: The `map_move` builtin and the `move <exit>` command

**Files:**
- Modify: `src/saga_trpg/rulebooks/engine.py`
- Modify: `src/saga_trpg/rulebooks/commands.py`
- Modify: `tests/test_map_rulebooks.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_map_rulebooks.py`:

```python
# --- map_move -----------------------------------------------------------------------------------


def test_move_follows_an_exit_and_records_patches_and_a_fact():
    spec = parse_rulebook(srd_with(HAND_MAP))
    runtime = engine(spec)
    entry = runtime.dispatch(move(spec, "move NORTH", "m1")).entry
    assert entry.committed.event.payload == {"exit": "north"}
    player = runtime.state.actors["player"]
    assert player.location == "guard" and player.visited_rooms == {"hall", "guard"}
    assert [(p.path, p.before, p.after) for p in entry.result.patches] == [
        ("actors.player.location", "hall", "guard"),
        ("actors.player.visited_rooms", ["hall"], ["guard", "hall"]),
    ]
    assert entry.result.facts == ["player: move through 북쪽 문 from 입구 홀 to 경비실."]
    assert entry.result.rolls == [] and runtime.state.simulated_time == 0


@pytest.mark.parametrize("text", ["move west", "move nowhere"])
def test_moves_that_do_not_leave_the_current_room_are_rejected_without_a_journal_entry(text):
    spec = parse_rulebook(srd_with(HAND_MAP))
    runtime = engine(spec)
    before = runtime.state.model_copy(deep=True)
    with pytest.raises(ValueError, match="입구 홀 has no exit"):
        runtime.dispatch(move(spec, text, "m1"))
    assert runtime.journal.entries() == [] and runtime.state == before


def test_move_in_a_world_without_a_map_is_rejected():
    data = json.loads(rulebook_path("srd-5.2.1", "0.5.0").read_text())
    data["actions"]["move"] = {"builtin": "map_move"}
    spec = parse_rulebook(data)
    with pytest.raises(ValueError, match="this world has no map"):
        engine(spec).dispatch(move(spec, "move north", "m1"))


def test_move_exit_keys_are_closed_identifiers():
    spec = parse_rulebook(srd_with(HAND_MAP))
    with pytest.raises(CommandError, match="move: exit"):
        parse_command(spec, actor_id="player", text="move '북쪽 문'")
    assert "move <exit>" in command_help(spec)


def test_moves_replay_from_the_journal_and_retries_reuse_the_entry():
    spec = parse_rulebook(srd_with(HAND_MAP))
    runtime = engine(spec)
    initial = runtime.state.model_copy(deep=True)
    runtime.dispatch(move(spec, "move north", "m1"))
    runtime.dispatch(move(spec, "move south", "m2"))
    runtime.dispatch(move(spec, "move east", "m3"))
    retry = runtime.dispatch(move(spec, "move east", "m3"))
    assert retry.replayed and len(runtime.journal.entries()) == 3
    assert replay_recorded_results(initial, runtime.journal.entries()) == runtime.state
    assert runtime.state.actors["player"].visited_rooms == {"hall", "guard", "store"}
```

- [ ] **Step 2: Run to see them fail**

Run: `$PY -m pytest tests/test_map_rulebooks.py -v -k "move"`
Expected: `KeyError: 'map_move'` from the command parser and missing `exit` payload fields.

- [ ] **Step 3: Implement**

Apply the remaining hunks of the Task 5 engine diff: the `map_move` payload fields in
`_payload_model`, the `map_move` branch in `_builtin` and the `_move` method. Then:

```diff
@@ -33,6 +33,7 @@
     "time_advance": ["amount", "unit"],
     "inventory_add": ["target_id", "item", "amount"],
     "inventory_remove": ["target_id", "item", "amount"],
+    "map_move": ["exit"],
 }
 _INTEGER = re.compile(r"^-?[0-9]{1,9}$")
```

- [ ] **Step 4: Run**

Run: `$PY -m pytest tests/test_map_rulebooks.py -v` → `19 passed`.
Run: `$PY -m pytest -q` → no failures.

- [ ] **Step 5: Commit**

```bash
git add src/saga_trpg/rulebooks/engine.py src/saga_trpg/rulebooks/commands.py tests/test_map_rulebooks.py
git commit -m "feat: map_move builtin and move <exit> command"
```

---

### Task 7: Footer location line

**Files:**
- Modify: `src/saga_trpg/rulebooks/display.py`
- Modify: `tests/test_map_rulebooks.py`

- [ ] **Step 1: Write the failing test** — append:

```python
# --- footer -------------------------------------------------------------------------------------


def test_footer_shows_the_viewers_room_and_exit_keys():
    data = srd_with(HAND_MAP)
    data["display"]["world"] = ["time", "location"]
    spec = parse_rulebook(data)
    state = DataRules(spec).example_world("w")
    assert render_display(spec, state)["world"] == ["⏱ 0s", "📍 입구 홀 · 출구 east, north"]
    no_map = DataRules(load_rulebook(rulebook_path("srd-5.2.1", "0.5.0"))).example_world("w")
    assert render_display(spec, no_map)["world"] == ["⏱ 0s"]
```

- [ ] **Step 2: Run to see it fail**

Run: `$PY -m pytest tests/test_map_rulebooks.py -v -k footer`
Expected: the world list is `['⏱ 0s']` without the location line.

- [ ] **Step 3: Implement** (`"location"` is already allowed in `display.world` by Task 5)

```diff
@@ -61,7 +61,16 @@
     return f"{name} ({', '.join(rest)})" if rest else str(name)
 
 
-def render_display(spec: RulebookSpec, state: WorldState) -> dict[str, Any]:
+def _location(state: WorldState, viewer: str) -> str | None:
+    actor = state.actors.get(viewer)
+    if state.map is None or actor is None or actor.location is None:
+        return None
+    room = state.map.rooms[actor.location]
+    exits = ", ".join(sorted(room.exits))
+    return f"📍 {room.name} · 출구 {exits}" if exits else f"📍 {room.name}"
+
+
+def render_display(spec: RulebookSpec, state: WorldState, viewer: str = "player") -> dict[str, Any]:
     actors = []
     for actor_id, actor in state.actors.items():
         items = [_actor_item(spec, actor, entry) for entry in spec.display.actor]
@@ -81,4 +90,8 @@
     world = []
     if "time" in spec.display.world:
         world.append(f"⏱ {format_time(state.simulated_time)}")
+    if "location" in spec.display.world:
+        location = _location(state, viewer)
+        if location:
+            world.append(location)
     return {"actors": actors, "records": records, "world": world}
```

- [ ] **Step 4: Run**

Run: `$PY -m pytest tests/test_map_rulebooks.py tests/test_rulebook_display.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/saga_trpg/rulebooks/display.py tests/test_map_rulebooks.py
git commit -m "feat: footer shows the player's room and exit keys"
```

---

### Task 8: Bundled `srd-5.2.1@0.6.0` and `fate-condensed@0.5.0`

New Risu chats pin the latest SRD, so after this task they start in a generated dungeon
(ADR 0009, Consequences). Bundled files are immutable per version: never edit `0.5.0`/`0.4.0`.

**Files:**
- Create: `src/saga_trpg/rulebooks/bundled/srd-5.2.1@0.6.0.json`
- Create: `src/saga_trpg/rulebooks/bundled/fate-condensed@0.5.0.json`
- Modify: `tests/test_map_rulebooks.py`, `tests/test_rulebooks.py`, `tests/test_rulebooks_fate.py`,
  `tests/test_rulebook_display.py`, `tests/test_risu_module.py`, `tests/test_risu_api.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_map_rulebooks.py`:

```python
# --- bundled versions ---------------------------------------------------------------------------


def test_new_bundled_versions_declare_move_and_an_example_map():
    assert (SRD_LATEST, FATE_LATEST) == ("0.6.0", "0.5.0")
    registry = RulesetRegistry()
    srd = registry.get("srd-5.2.1", "0.6.0")
    world = srd.example_world("w")
    assert world.map.generator.name == "saga-rooms"
    assert world.actors["player"].location == world.map.start_room
    assert "move" in srd.manifest.event_payload_schemas
    fate = registry.get("fate-condensed", "0.5.0").example_world("w")
    assert fate.map.start_room == "harbor" and fate.map.generator is None
    for older in (("srd-5.2.1", "0.5.0"), ("fate-condensed", "0.4.0")):
        assert registry.get(*older).example_world("w").map is None


def test_a_bundled_generated_world_can_walk_every_exit_of_its_start_room():
    spec = load_rulebook(rulebook_path("srd-5.2.1", "0.6.0"))
    runtime = engine(spec, world_id="walk")
    rooms = runtime.state.map.rooms
    start = runtime.state.map.start_room
    for index, key in enumerate(sorted(rooms[start].exits)):
        runtime.dispatch(
            GameEvent(event_id=f"go{index}", kind="move", actor_id="player", payload={"exit": key})
        )
        here = rooms[runtime.state.actors["player"].location]
        back = next(k for k, e in here.exits.items() if e.to_room == start)
        runtime.dispatch(
            GameEvent(
                event_id=f"back{index}", kind="move", actor_id="player", payload={"exit": back}
            )
        )
    assert runtime.state.actors["player"].location == start
```

- [ ] **Step 2: Run to see it fail**

Run: `$PY -m pytest tests/test_map_rulebooks.py -v -k bundled`
Expected: `('0.5.0', '0.4.0') == ('0.6.0', '0.5.0')` fails; `0.6.0` file not found.

- [ ] **Step 3: Create the two bundled files from their predecessors**

```bash
$PY - <<'EOF'
import json
B = "src/saga_trpg/rulebooks/bundled/"
d = json.load(open(B + "srd-5.2.1@0.5.0.json", encoding="utf-8"))
d["version"] = "0.6.0"
d["actions"]["move"] = {"builtin": "map_move"}
d["example_world"]["map"] = {"generate": {"seed": "srd-demo", "rooms": [6, 10]}}
d["display"]["world"] = ["time", "location"]
d["implemented"].append("room-graph map generated per world; move between connected rooms")
d["not_implemented"] = [
    "initiative, range, tactical movement and action economy" if x == "initiative, range, movement and action economy" else x
    for x in d["not_implemented"]
]
d["narration"] = d["narration"].replace("version 0.5.0.", "version 0.6.0.") + (
    "\nEach world starts in a generated dungeon. Move with move <exit>, where the exit key is "
    "shown in the status line. Describe only the current room and what the map block lists; "
    "never describe rooms behind ??? exits."
)
open(B + "srd-5.2.1@0.6.0.json", "w", encoding="utf-8").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")

f = json.load(open(B + "fate-condensed@0.4.0.json", encoding="utf-8"))
f["version"] = "0.5.0"
f["actions"]["move"] = {"builtin": "map_move"}
f["example_world"]["map"] = {
    "start_room": "harbor",
    "rooms": {
        "harbor": {"name": "부두", "description": "안개 낀 부두. 밧줄에 묶인 작은 배가 흔들린다.",
                   "exits": {"north": {"to_room": "warehouse", "label": "북쪽 창고 문", "kind": "door"},
                             "east": {"to_room": "lighthouse", "label": "동쪽 해안길", "kind": "open"}}},
        "warehouse": {"name": "창고", "description": "젖은 그물과 소금 냄새가 가득하다.",
                      "exits": {"south": {"to_room": "harbor", "label": "남쪽 문", "kind": "door"}}},
        "lighthouse": {"name": "등대 아래", "description": "파도가 바위에 부딪혀 물보라가 튄다.",
                       "exits": {"west": {"to_room": "harbor", "label": "서쪽 해안길", "kind": "open"},
                                 "up": {"to_room": "lamp_room", "label": "위쪽 나선 계단", "kind": "door"}}},
        "lamp_room": {"name": "등대 꼭대기", "description": "꺼진 등불 옆으로 바다가 내려다보인다.",
                      "exits": {"down": {"to_room": "lighthouse", "label": "아래쪽 나선 계단", "kind": "door"}}},
    },
}
f["display"]["world"] = ["location"]
f["implemented"].append("hand-authored room-graph map; move between connected rooms")
f["narration"] = f["narration"].replace("version 0.4.0.", "version 0.5.0.") + (
    "\nMove with move <exit>, where the exit key is shown in the status line. Describe only the "
    "current room and what the map block lists; never describe rooms behind ??? exits."
)
open(B + "fate-condensed@0.5.0.json", "w", encoding="utf-8").write(json.dumps(f, indent=2, ensure_ascii=False) + "\n")
EOF
$PY -m saga_trpg.rulebooks check src/saga_trpg/rulebooks/bundled/srd-5.2.1@0.6.0.json
$PY -m saga_trpg.rulebooks check src/saga_trpg/rulebooks/bundled/fate-condensed@0.5.0.json
```

Expected: both checks print the rulebook summary and exit 0.

- [ ] **Step 4: Run the suite and read the failures**

Run: `$PY -m pytest -q`
Expected: the new bundled test passes and exactly these existing tests fail, all because they
pin "latest" to the old version or fix the footer's line count:

```text
tests/test_risu_api.py::test_bridge_worlds_on_040_apply_condition_effects_from_the_rulebook
tests/test_risu_api.py::test_response_carries_rendered_display_lines_from_the_rulebook
tests/test_risu_module.py::test_module_json_is_the_plain_import_shape_with_two_lorebook_entries
tests/test_risu_module.py::test_module_round_trips_through_the_lorebook_finder
tests/test_risu_module.py::test_store_persists_validated_rulebooks_and_refuses_conflicting_content
tests/test_risu_module.py::test_store_refuses_to_shadow_a_bundled_rulebook_version
tests/test_rulebook_display.py::test_bundled_rulebooks_declare_what_the_footer_shows
tests/test_rulebooks.py::test_srd_rulebook_loads_and_exposes_closed_payload_models
tests/test_rulebooks.py::test_check_command_reports_problems_in_plain_text
tests/test_rulebooks.py::test_registry_serves_srd_040_from_the_rulebook_and_keeps_python_versions
tests/test_rulebooks_fate.py::test_fate_rulebook_loads_with_records_and_graded_outcomes
```

Any other failure is a real regression: stop and investigate.

- [ ] **Step 5: Update the pins**

Tests that are about "the latest bundled rulebook" move to the new versions; tests about
module export/import pin the old versions explicitly so the next release does not move them.

`tests/test_rulebooks.py`:

```diff
@@ -61,9 +61,10 @@
 
 def test_srd_rulebook_loads_and_exposes_closed_payload_models():
     spec = load_rulebook(SRD_PATH)
-    assert (spec.id, spec.version) == ("srd-5.2.1", "0.5.0")
+    assert (spec.id, spec.version) == ("srd-5.2.1", "0.6.0")
     rules = DataRules(spec)
     assert set(rules.payload_models) == {
+        "move",
         "ability_check",
         "saving_throw",
         "attack",
@@ -111,7 +112,7 @@
     assert main(["check", str(path)]) == 1
     assert "actions.attack.check" in capsys.readouterr().err
     assert main(["check", str(SRD_PATH)]) == 0
-    assert "srd-5.2.1@0.5.0" in capsys.readouterr().out
+    assert "srd-5.2.1@0.6.0" in capsys.readouterr().out
 
 
 # --- equivalence with the hand-written SRD module --------------------------------------------
@@ -333,21 +334,23 @@
     from saga_trpg.modules.catalog import SRD_LATEST, RulesetRegistry
 
     registry = RulesetRegistry()
-    assert SRD_LATEST == "0.5.0"
+    assert SRD_LATEST == "0.6.0"
     latest = registry.get("srd-5.2.1", SRD_LATEST)
     assert isinstance(latest.rules, DataRules)
-    assert latest.manifest.ruleset_version == "0.5.0"
+    assert latest.manifest.ruleset_version == "0.6.0"
     assert "condition effects on attack, check and save rolls" in " ".join(
         latest.manifest.implemented
     )
     assert set(latest.manifest.event_payload_schemas) == set(latest.rules.payload_models)
     assert latest.example_world("w").actors["player"].stats["strength"] == 16
-    assert latest.example_world("w") == registry.get("srd-5.2.1", "0.3.0").example_world("w")
+    assert registry.get("srd-5.2.1", "0.5.0").example_world("w") == registry.get(
+        "srd-5.2.1", "0.3.0"
+    ).example_world("w")
     for version in ("0.1.0", "0.2.0", "0.3.0"):
         assert isinstance(registry.get("srd-5.2.1", version).rules, SRD521Rules)
     assert sorted(
         m.ruleset_version for m in registry.manifests() if m.ruleset_id == "srd-5.2.1"
-    ) == ["0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"]
+    ) == ["0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0"]
 
 
 @pytest.mark.parametrize(
```

`tests/test_rulebooks_fate.py`:

```diff
@@ -19,11 +19,17 @@
 
 def test_fate_rulebook_loads_with_records_and_graded_outcomes():
     spec = load_rulebook(FATE_PATH)
-    assert (spec.id, spec.version) == ("fate-condensed", "0.4.0")
+    assert (spec.id, spec.version) == ("fate-condensed", "0.5.0")
     assert set(spec.records) == {"fate_aspects", "fate_boosts"}
     assert spec.records["fate_aspects"].unique == ["name"]
     rules = fate_rules()
-    assert set(rules.payload_models) == {"overcome", "create_advantage", "invoke", "use_boost"}
+    assert set(rules.payload_models) == {
+        "overcome",
+        "create_advantage",
+        "invoke",
+        "use_boost",
+        "move",
+    }
     schema = rules.payload_models["overcome"].model_json_schema()
     assert schema["properties"]["boost_name"]["default"] == "Temporary advantage"
     assert "advantage" not in schema["properties"]
```

`tests/test_rulebook_display.py`:

```diff
@@ -13,10 +13,10 @@
 
 def test_bundled_rulebooks_declare_what_the_footer_shows():
     assert SRD.display.actor == ["hp", "conditions", "inventory"]
-    assert SRD.display.world == ["time"]
+    assert SRD.display.world == ["time", "location"]
     assert FATE.display.actor == ["skill:*"]
     assert FATE.display.records == ["fate_aspects", "fate_boosts"]
-    assert FATE.display.world == []
+    assert FATE.display.world == ["location"]
 
 
 def test_srd_display_lines_follow_the_declaration():
```

`tests/test_risu_module.py`:

```diff
@@ -14,8 +14,9 @@
 )
 from saga_trpg.rulebooks.store import RulebookStore
 
-SRD_TEXT = rulebook_path("srd-5.2.1").read_text(encoding="utf-8")
-FATE_TEXT = rulebook_path("fate-condensed").read_text(encoding="utf-8")
+# Export and import are tested on fixed versions so a new bundled release does not move them.
+SRD_TEXT = rulebook_path("srd-5.2.1", "0.5.0").read_text(encoding="utf-8")
+FATE_TEXT = rulebook_path("fate-condensed", "0.4.0").read_text(encoding="utf-8")
 
 
 def sha(text):
@@ -34,7 +35,9 @@
     narration, data = module["lorebook"]
     assert narration["comment"] == "saga:narration:srd-5.2.1@0.5.0"
     assert narration["alwaysActive"] is True and narration["mode"] == "constant"
-    assert narration["content"] == narration_text(load_rulebook(rulebook_path("srd-5.2.1")))
+    assert narration["content"] == narration_text(
+        load_rulebook(rulebook_path("srd-5.2.1", "0.5.0"))
+    )
     assert "Not implemented:" in narration["content"]
     assert data["comment"] == f"saga:rulebook:srd-5.2.1@0.5.0:{sha(SRD_TEXT)}"
     assert data["useRegex"] is True and data["key"] == "(?!)"
```

`tests/test_risu_api.py`:

```diff
@@ -823,7 +823,7 @@
         response = client.post("/v1/chat/completions", json=request(*turns[: index + 1]))
         assert response.status_code == 200, response.text
     result = response.json()["saga"]
-    assert repo.ruleset_for_world(result["world_id"]).ruleset_version == "0.5.0"
+    assert repo.ruleset_for_world(result["world_id"]).ruleset_version == SRD_LATEST
     d20s = [roll for roll in result["rolls"] if roll["roll_key"].startswith("attack:")]
     assert len(d20s) == 2
     assert any("prone" in line for line in result["trace"])
@@ -931,7 +931,8 @@
     srd = client.post("/v1/chat/completions", json=request(("u1", "inflict opponent prone")))
     display = srd.json()["saga"]["display"]
     assert display["actors"][1] == {"id": "opponent", "items": ["30/30", "prone"]}
-    assert display["world"] == ["⏱ 0s"] and display["records"] == []
+    assert display["world"][0] == "⏱ 0s" and display["records"] == []
+    assert display["world"][1].startswith("📍 입구 홀 · 출구 ")
     fate = client.post(
         "/v1/chat/completions",
         json=fate_request(("u1", 'create_advantage notice 1 "Mapped route"')),
@@ -940,7 +941,7 @@
     assert display["actors"] == [
         {"id": "player", "items": ["athletics +1, investigate +2, notice +3"]}
     ]
-    assert display["world"] == []
+    assert display["world"] == ["📍 부두 · 출구 east, north"]
     if fate.json()["saga"]["resolution"]["outcome"] in {"success", "success_with_style"}:
         assert display["records"][0]["label"] == "fate_aspects"
         assert display["records"][0]["items"][0].startswith("Mapped route")
```

- [ ] **Step 6: Run**

Run: `$PY -m pytest -q` → no failures.

- [ ] **Step 7: Commit**

```bash
git add src/saga_trpg/rulebooks/bundled/srd-5.2.1@0.6.0.json src/saga_trpg/rulebooks/bundled/fate-condensed@0.5.0.json tests/
git commit -m "feat: srd-5.2.1@0.6.0 and fate-condensed@0.5.0 with maps and move"
```

---

### Task 9: Narrator sees the known map, never the full map

**Files:**
- Modify: `src/saga_trpg/ports/narrator.py`
- Modify: `src/saga_trpg/adapters/narrator/vertex.py`
- Modify: `src/saga_trpg/adapters/risu/openai_proxy.py`
- Modify: `tests/test_risu_api.py`, `tests/test_vertex_narration.py`

Today the Vertex narrator receives `state.model_dump()` whole, so without this task the full
map would reach the model. This task is not optional.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risu_api.py`:

```python
# --- maps (ADR 0009) -------------------------------------------------------------------------


def test_new_chats_start_on_a_generated_map_and_move_commits_once(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    first = client.post("/v1/chat/completions", json=request(("u1", "wait 1 round")))
    assert first.status_code == 200, first.text
    saved = first.json()["saga"]
    state = repo.get_state(saved["world_id"], branch_id=saved["branch_id"])
    start = state.map.start_room
    assert state.actors["player"].location == start
    key = sorted(state.map.rooms[start].exits)[0]
    destination = state.map.rooms[start].exits[key].to_room
    turns = [("u1", "wait 1 round"), ("u2", f"move {key}")]
    moved = client.post("/v1/chat/completions", json=request(*turns))
    assert moved.status_code == 200, moved.text
    result = moved.json()["saga"]
    assert result["state"]["actors"]["player"]["location"] == destination
    assert result["display"]["world"][1].startswith(
        "📍 " + state.map.rooms[destination].name + " · 출구 "
    )
    entries = repo.entries(result["world_id"], branch_id=result["branch_id"])
    for _ in range(3):
        again = client.post("/v1/chat/completions", json=request(*turns)).json()["saga"]
        assert again == {**result, "replayed": True}
    assert repo.entries(result["world_id"], branch_id=result["branch_id"]) == entries
    assert dice.calls == 0


def test_a_move_without_a_matching_exit_is_rejected_without_a_commit(tmp_path):
    repo, _, _, client = setup(tmp_path)
    first = client.post("/v1/chat/completions", json=request(("u1", "wait 1 round"))).json()
    world_id, branch_id = first["saga"]["world_id"], first["saga"]["branch_id"]
    rejected = client.post(
        "/v1/chat/completions", json=request(("u1", "wait 1 round"), ("u2", "move nowhere"))
    )
    assert rejected.status_code == 422 and "has no exit 'nowhere'" in rejected.json()["detail"]
    assert len(repo.entries(world_id, branch_id=branch_id)) == 1
```

Append to `tests/test_vertex_narration.py`:

```python
def test_vertex_prompt_carries_the_known_map_and_never_the_full_map(
    tmp_path, monkeypatch, fake_credentials
):
    from saga_trpg.domain.maps import known_map

    repo, dice, intent, client = setup(tmp_path)
    seen = []

    def remote(req):
        seen.append(json.loads(req.content))
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "prose"}]}}]}
        )

    install_vertex(monkeypatch, remote)
    response = vertex_post(client)
    assert response.status_code == 200, response.text
    saga = response.json()["saga"]
    state = repo.get_state(saga["world_id"], branch_id=saga["branch_id"])
    texts = [part["text"] for content in seen[-1]["contents"] for part in content["parts"]]
    committed = json.loads(texts[-1])
    assert "map" not in committed["state"]
    scene = [t for t in texts if t.startswith("Map (everything the player knows")]
    assert scene and "현재 위치: 입구 홀" in scene[0]
    view = known_map(state, "player")
    shown = {view.room_name, *(e.destination for e in view.exits)}
    hidden = [room.name for room in state.map.rooms.values() if room.name not in shown]
    assert hidden, "the generated demo map should have rooms the player has not seen"
    prompt = "\n".join(texts)
    assert not any(name in prompt for name in hidden)
```

- [ ] **Step 2: Run to see them fail**

Run: `$PY -m pytest tests/test_risu_api.py tests/test_vertex_narration.py -q -k "map or move"`
Expected: the bridge tests pass already (Tasks 5–8); the Vertex test fails with
`assert "map" not in committed["state"]`.

- [ ] **Step 3: Implement**

```diff
@@ -15,6 +15,8 @@
     result: RuleResult
     memories: list[MemoryHit] = Field(default_factory=list)
     recent_dialogue: list[str] = Field(default_factory=list)
+    # The player's known map (ADR 0009). Narrators get this projection, never the full map.
+    scene: str | None = None
 
 
 class NarratorPort(Protocol):
```

```diff
@@ -110,7 +110,8 @@
             "world_id": request.world_id,
             "event_id": request.event.event_id,
             "facts": request.result.facts,
-            "state": request.result.state.model_dump(mode="json"),
+            # The full map stays with the runtime; the narrator sees only `scene` (ADR 0009).
+            "state": request.result.state.model_dump(mode="json", exclude={"map"}),
             "rolls": [roll.model_dump(mode="json") for roll in request.result.rolls],
             "resolution": request.result.resolution.model_dump(mode="json")
             if request.result.resolution
@@ -153,6 +154,24 @@
                     if request.memories
                     else []
                 ),
+                *(
+                    [
+                        {
+                            "role": "user",
+                            "parts": [
+                                {
+                                    "text": (
+                                        "Map (everything the player knows; describe nothing "
+                                        "beyond it, and nothing behind ??? exits):\n"
+                                        + request.scene
+                                    )
+                                }
+                            ],
+                        }
+                    ]
+                    if request.scene
+                    else []
+                ),
                 {
                     "role": "user",
                     "parts": [{"text": json.dumps(committed, ensure_ascii=False)}],
```

```diff
@@ -28,6 +28,7 @@
     SQLiteWorldRepository,
 )
 from saga_trpg.domain.events import GameEvent
+from saga_trpg.domain.maps import scene_text
 from saga_trpg.domain.models import WorldState
 from saga_trpg.domain.tools import ToolCall
 from saga_trpg.modules.srd_5_2_1.rules import ROLL_ACTIONS
@@ -610,6 +611,7 @@
             result=saved.result.model_copy(deep=True),
             memories=memories,
             recent_dialogue=request.narration.context if request.narration else [],
+            scene=scene_text(saved.result.state, turn.actor_id),
         )
         try:
             prose = await narrator.render(narration)
```

Update the existing assertion that compared the narrator's state to the whole stored state:

```diff
@@ -80,7 +80,8 @@
         event_id = committed["event_id"]
         saved = repo.get_event(committed["world_id"], event_id)
         assert saved is not None
-        assert committed["state"] == saved.result.state.model_dump(mode="json")
+        # Everything but the map; the narrator gets the known map separately (ADR 0009).
+        assert committed["state"] == saved.result.state.model_dump(mode="json", exclude={"map"})
         assert str(req.url) == (
             "https://aiplatform.googleapis.com/v1/projects/saga-test-project/locations/"
             "global/publishers/google/models/gemini-3.8-flash:generateContent"
```

- [ ] **Step 4: Run**

Run: `$PY -m pytest -q` → `597 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/saga_trpg/ports/narrator.py src/saga_trpg/adapters/narrator/vertex.py src/saga_trpg/adapters/risu/openai_proxy.py tests/test_risu_api.py tests/test_vertex_narration.py
git commit -m "feat: narrator gets the player's known map; the full map stays in the runtime"
```

---

### Task 10: Schemas and author docs

**Files:**
- Regenerate: `schemas/world.schema.json`
- Modify: `docs/RULEBOOKS.md`

- [ ] **Step 1: Regenerate schemas**

Run: `PYTHONPATH=src $PY scripts/export_schemas.py && git diff --stat schemas`
Expected: only `schemas/world.schema.json` (and `journal-entry.schema.json`, which embeds
`WorldState`) change, gaining `map`, `location`, `visited_rooms`, `Exit`, `Room`,
`MapGenerator`, `MapState`.

- [ ] **Step 2: Document `map_move` in `docs/RULEBOOKS.md`**

In section 4, after the paragraph that ends "Nothing happens automatically at 0 HP unless your
actions say so.", add:

```markdown
One more moves the acting actor between rooms: `"move": { "builtin": "map_move" }`. Its only
input is `exit`, the key of an exit of the actor's current room (`move north`). The key is
checked against the room when the event is applied, so a command never depends on where the
actor stands; a move from a world without a map, or through an exit the room does not have,
is rejected without a journal entry. Moving does not advance time.
```

- [ ] **Step 3: Document maps in section 7**

At the end of section 7 (`example_world`), add:

````markdown
`map` (optional) gives the world a room graph ([ADR 0009](adr/0009-locations-and-map-movement.md)).
Hand-authored:

```json
"map": {
  "start_room": "hall",
  "rooms": {
    "hall":  { "name": "입구 홀", "description": "…",
               "exits": { "north": { "to_room": "guard", "label": "북쪽 문", "kind": "door" } } },
    "guard": { "name": "경비실",
               "exits": { "south": { "to_room": "hall", "label": "남쪽 문", "kind": "door" } } }
  },
  "placements": { "opponent": "guard" }
}
```

Room ids and exit keys are lower-case identifiers; exits are one-way, so a two-way passage is
an exit in each room. `kind` is `open` (the next room's name is visible) or `door` (it shows as
`???` until visited). Every room must be reachable from `start_room`. Actors without a
placement start in `start_room`.

Generated: `"map": { "generate": { "seed": "my-dungeon", "rooms": [6, 10] } }` builds 2..30
rooms from the seed and the world id, so each RisuAI chat gets its own dungeon and the same
chat always the same one. The generated map is stored when the world is created; later
changes to the generator never alter it.
````

- [ ] **Step 4: Document the footer entry in section 8**

Replace the `"world": ["time"]` line of the section 8 example with:

```markdown
  "world": ["time", "location"]                  // world-level items; [] hides time; "location" shows the player's room and exit keys
```

- [ ] **Step 5: Commit**

```bash
git add schemas docs/RULEBOOKS.md
git commit -m "docs: maps and map_move for rulebook authors; regenerate schemas"
```

---

### Task 11: Verification, ADR acceptance and progress report

**Files:**
- Modify: `docs/adr/0009-locations-and-map-movement.md`
- Create: `docs/progress/locations-map-v1.md`
- Modify: `docs/progress/README.md`

- [ ] **Step 1: Full verification**

```bash
$PY -m pytest -q
$PY -m compileall -q src
$RUFF check .
$RUFF format --check .
```

Expected: `597 passed`; compileall silent; ruff clean.

- [ ] **Step 2: CLAUDE.md self-review** — answer each in the progress report:

1. Dice/tools twice on rerender? No: moves have no dice; `test_new_chats_start_on_a_generated_map_and_move_commits_once` replays three times with an unchanged journal and `dice.calls == 0`.
2. Narration mutating state? No: the narrator receives a string projection after commit.
3. Wall-clock in replay? No: the generator is seeded from rulebook seed and world id; maps are stored at creation.
4. NMOS duplication? No: the map lives only in `WorldState`; memory gets the existing event projection.
5. Benchmark comparability? Unchanged: the runner builds worlds from scenario `initial_world`, never from `example_world`.

- [ ] **Step 3: Mark ADR 0009 accepted**

Change its status line to `Status: accepted (<date of merge>)` and replace the "Evidence (to be
added with the implementation)" section with:

```markdown
## Evidence

`tests/test_state_compat.py` (golden hash and stored JSON of a map-less world, written before
any map code), `tests/test_maps.py` (validation, projection, generator determinism and a pinned
map digest, reachability and two-way exits across 800 generated maps),
`tests/test_map_rulebooks.py` (loader checks by entry path, example worlds, `map_move`
acceptance, rejection without a journal entry, replay and retries, footer, bundled versions),
`tests/test_risu_api.py` (a new chat starts on a generated map; a move commits once across
regenerations; a bad exit is rejected) and `tests/test_vertex_narration.py` (the prompt carries
the known map and none of the unseen room names). Full suite: 597 passed.
```

- [ ] **Step 4: Write `docs/progress/locations-map-v1.md`**

```markdown
# Locations and map movement v1

Date: <today> (Asia/Seoul). Status: implemented and tested offline; not deployed to 6011.
Spec: [ADR 0009](../adr/0009-locations-and-map-movement.md). Tracks SAGA-44 (first slice).

## Changed

- `WorldState.map` (room graph) and actor `location`/`visited_rooms`, omitted from
  serialization while empty; map-less worlds keep their exact JSON and state hash.
- `domain/maps.py`: validation, the known-map projection and its text block.
- `domain/mapgen.py`: seeded generator `saga-rooms` v1 (2..30 rooms, spanning tree plus loops,
  Korean room table, start room is the entrance hall).
- Rulebooks: `example_world.map` (hand-authored or `generate`), builtin `map_move`, command
  `move <exit>`, footer entry `location`.
- Bundled `srd-5.2.1@0.6.0` (generated map per chat) and `fate-condensed@0.5.0` (harbor map).
  New Risu chats now start in a generated dungeon; existing chats keep their version.
- Vertex narration receives the known map as its own part and the committed state without
  `map`.

## Verification

- `pytest` 597 passed; `ruff check`, `ruff format --check`, `compileall` clean.
- CLAUDE.md checks: (copy the five answers from Task 11 Step 2).

## Unresolved

- Natural-language movement ("북쪽으로 간다") needs an interpreter contract change with tape
  identity design; until then players type `move <exit>` from the footer.
- No live check on 6011; the plugin footer was not inspected in a real RisuAI client.
- Discovery by checks, locked doors, loot, NPC movement, play-time generation and a visual map
  are later slices (ADR 0009, Not in this slice).
```

- [ ] **Step 5: Point the progress index at it**

In `docs/progress/README.md`, after the line that starts "Current implementation checkpoints:",
add:

```markdown
Map checkpoint: [Locations and map movement v1](locations-map-v1.md).
```

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0009-locations-and-map-movement.md docs/progress/locations-map-v1.md docs/progress/README.md
git commit -m "docs: ADR 0009 accepted; progress report for locations and map movement v1"
```

---

### Task 12: PR and tracking

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin claude/risuai-mcp-map-generation-cfaca0
gh pr create --base main --title "feat: locations as a room graph with map_move (ADR 0009)" --body-file docs/progress/locations-map-v1.md
```

Append the attribution line `🤖 Generated with [Claude Code](https://claude.com/claude-code)` to the PR body.

- [ ] **Step 2: Plane**

On SAGA-44 add a comment linking the PR and the progress report and stating that this is the
first slice. Create two Backlog issues in the "Product proposals — TRPG features" module, as
children of SAGA-44: "자연어 이동 (해석기 계약·tape 식별자 설계 포함)" and "지도 시각화
(known-map 투영 기반 SVG/HTML 렌더러, '지도 본다' 조회)". Do not change SAGA-44's state until
the PR is merged.
