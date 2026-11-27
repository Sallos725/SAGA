"""Room-graph helpers: validation, the player's known map and its text form (ADR 0009).

Everything here is pure. The known map is a projection of committed state; it is recomputed
on every read and never stored, so rerendering a reply cannot change it.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

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
    locked: bool = False


class KnownMap(BaseModel):
    room_id: str
    room_name: str
    description: str
    occupants: list[str]
    exits: list[KnownExit]
    visited: list[str]
    loot: dict[str, int] = Field(default_factory=dict)
    discovered_clues: dict[str, str] = Field(default_factory=dict)


def known_map(state: WorldState, actor_id: str) -> KnownMap | None:
    """What `actor_id` knows: the current room in full, exits with destinations only where
    visited or seen through an open unlocked passage, available loot, discovered clues,
    and the names of visited rooms."""
    actor = state.actors.get(actor_id)
    if state.map is None or actor is None or actor.location is None:
        return None
    rooms = state.map.rooms
    room = rooms[actor.location]
    exits = []
    for key in sorted(room.exits):
        exit_ = room.exits[key]
        visited = exit_.to_room in actor.visited_rooms
        can_see = visited or (exit_.kind == "open" and not exit_.locked)
        destination = rooms[exit_.to_room].name if can_see else UNKNOWN
        exits.append(
            KnownExit(
                key=key,
                label=exit_.label,
                kind=exit_.kind,
                destination=destination,
                visited=visited,
                locked=exit_.locked,
            )
        )
    occupants = sorted(
        other_id
        for other_id, other in state.actors.items()
        if other_id != actor_id and other.location == actor.location
    )
    visited_names = sorted(rooms[room_id].name for room_id in actor.visited_rooms)
    loot = {k: v for k, v in sorted(room.loot.items()) if v > 0}
    discovered = {
        clue_id: room.clues[clue_id]
        for clue_id in sorted(room.discovered_clues)
        if clue_id in room.clues
    }
    return KnownMap(
        room_id=actor.location,
        room_name=room.name,
        description=room.description,
        occupants=occupants,
        exits=exits,
        visited=visited_names,
        loot=loot,
        discovered_clues=discovered,
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
    if view.loot:
        items = ", ".join(f"{k}×{v}" for k, v in sorted(view.loot.items()))
        lines.append("여기 놓인 물품: " + items)
    if view.discovered_clues:
        clues = "; ".join(f"{k}: {v}" for k, v in sorted(view.discovered_clues.items()))
        lines.append("발견한 단서: " + clues)
    lines.append("출구:")
    for exit_ in view.exits:
        tags = []
        if exit_.locked:
            tags.append("잠김")
        if exit_.visited:
            tags.append("가봄")
        note = f" ({', '.join(tags)})" if tags else ""
        lines.append(f"- {exit_.key}: {exit_.label} → {exit_.destination}{note}")
    if not view.exits:
        lines.append("- 없음")
    lines.append("가본 곳: " + ", ".join(view.visited))
    return "\n".join(lines)
