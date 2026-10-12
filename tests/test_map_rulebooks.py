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


# --- loader ----------------------------------------------------------------------------------


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


def test_hand_authored_maps_have_a_room_limit():
    rooms = {
        f"r{index}": {
            "name": f"방 {index}",
            "exits": {"next": {"to_room": f"r{index + 1}", "label": "앞"}},
        }
        for index in range(200)
    }
    rooms["r200"] = {"name": "끝"}
    with pytest.raises(RulebookError, match="example_world.map.rooms: at most 200 rooms"):
        parse_rulebook(srd_with({"start_room": "r0", "rooms": rooms}))


@pytest.mark.parametrize("rooms", [[1, 4], [8, 6], [2, 31]])
def test_generator_room_bounds_are_checked(rooms):
    with pytest.raises(RulebookError, match="example_world.map.generate.rooms"):
        parse_rulebook(srd_with({"generate": {"seed": "x", "rooms": rooms}}))


# --- worlds ----------------------------------------------------------------------------------


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


def test_worlds_never_share_the_rulebooks_map_objects():
    spec = parse_rulebook(srd_with(HAND_MAP))
    rules = DataRules(spec)
    first = rules.example_world("a")
    first.map.rooms["hall"].name = "changed"
    first.map.rooms["hall"].exits["north"].label = "changed"
    assert rules.example_world("b").map.rooms["hall"].name == "입구 홀"
    assert spec.example_world.map.rooms["hall"].exits["north"].label == "북쪽 문"


def test_worlds_without_a_map_are_unchanged():
    state = DataRules(load_rulebook(rulebook_path("srd-5.2.1", "0.5.0"))).example_world("w")
    assert state.map is None
    assert all(actor.location is None for actor in state.actors.values())


# --- map_move --------------------------------------------------------------------------------


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


@pytest.mark.parametrize("condition", ["paralyzed", "petrified", "incapacitated"])
def test_actors_that_cannot_act_cannot_move(condition):
    spec = parse_rulebook(srd_with(HAND_MAP))
    runtime = engine(spec)
    runtime.state.actors["player"].conditions.add(condition)
    with pytest.raises(ValueError, match=f"player cannot act while {condition}"):
        runtime.dispatch(move(spec, "move north", "m1"))
    assert runtime.journal.entries() == [] and runtime.state.actors["player"].location == "hall"


def test_an_actor_off_the_map_cannot_move():
    spec = parse_rulebook(srd_with(HAND_MAP))
    runtime = engine(spec)
    runtime.state.actors["player"].location = None
    with pytest.raises(ValueError, match="player is not on the map"):
        runtime.dispatch(move(spec, "move north", "m1"))


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


# --- footer ----------------------------------------------------------------------------------


def test_footer_shows_the_viewers_room_and_exit_keys():
    data = srd_with(HAND_MAP)
    data["display"]["world"] = ["time", "location"]
    spec = parse_rulebook(data)
    state = DataRules(spec).example_world("w")
    assert render_display(spec, state)["world"] == ["⏱ 0s", "📍 입구 홀 · 출구 east, north"]
    no_map = DataRules(load_rulebook(rulebook_path("srd-5.2.1", "0.5.0"))).example_world("w")
    assert render_display(spec, no_map)["world"] == ["⏱ 0s"]


# --- bundled versions ------------------------------------------------------------------------


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
