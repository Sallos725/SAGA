"""Room-graph validation, the known-map projection and the seeded generator (ADR 0009)."""

import hashlib

import pytest

from saga_trpg.domain.mapgen import MAX_ROOMS, OPPOSITE, ROOMS, generate_map
from saga_trpg.domain.maps import (
    known_map,
    map_problems,
    reachable,
    render_known_map,
    scene_text,
)
from saga_trpg.domain.models import ActorState, Exit, MapState, Room, WorldState

# generate_map("srd-demo|golden", min_rooms=6, max_rooms=10), GENERATOR_VERSION "1".
GOLDEN_MAP_SHA256 = "ce760c11178c7e15e5b17c18f5df0f939485157f8afba030dd367e51d7dfbff6"


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


def test_returning_marks_visited_exits_and_keeps_far_rooms_in_the_visited_list():
    view = known_map(world(location="store", visited=("hall", "guard", "store")), "player")
    assert [(e.key, e.destination, e.visited) for e in view.exits] == [("west", "입구 홀", True)]
    assert view.visited == ["경비실", "입구 홀", "창고"]
    assert "- west: 서쪽 통로 → 입구 홀 (가봄)" in render_known_map(view)


def test_no_map_or_no_location_means_no_scene():
    assert scene_text(WorldState(world_id="w"), "player") is None
    unplaced = world()
    unplaced.actors["player"].location = None
    assert scene_text(unplaced, "player") is None
    assert scene_text(world(), "nobody") is None


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
    looped = False
    for seed in range(200):
        game_map = generate_map(f"seed-{seed}", min_rooms=low, max_rooms=high)
        assert low <= len(game_map.rooms) <= high
        assert map_problems(game_map) == []
        names = [room.name for room in game_map.rooms.values()]
        assert len(set(names)) == len(names)
        links = 0
        for room_id, room in game_map.rooms.items():
            assert len(room.exits) <= 6
            # At most one link between two rooms, and each exit returns along its opposite key.
            assert len({exit_.to_room for exit_ in room.exits.values()}) == len(room.exits)
            for key, exit_ in room.exits.items():
                back = game_map.rooms[exit_.to_room].exits.get(OPPOSITE[key])
                assert back is not None
                assert (back.to_room, back.kind) == (room_id, exit_.kind)
            links += len(room.exits)
        looped |= links // 2 > len(game_map.rooms) - 1
    # Loop attempts start at four rooms; below that every map is a tree.
    assert looped or high < 4


@pytest.mark.parametrize("bounds", [(1, 5), (5, 4), (2, 31)])
def test_out_of_range_room_counts_are_refused(bounds):
    with pytest.raises(ValueError, match="room count"):
        generate_map("x", min_rooms=bounds[0], max_rooms=bounds[1])
