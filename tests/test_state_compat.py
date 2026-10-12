"""Worlds saved before a state field existed keep their exact JSON and hash (ADR 0009)."""

import hashlib

from saga_trpg.domain.models import ActorState, Exit, MapState, Room, WorldState
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


def test_unplaced_actor_fields_are_omitted_from_both_dumps():
    actor = ActorState(actor_id="a")
    assert "location" not in actor.model_dump()
    assert "visited_rooms" not in actor.model_dump()
    assert "location" not in actor.model_dump_json()
    assert "visited_rooms" not in actor.model_dump_json()


def test_a_map_and_placement_are_serialized_sorted_and_round_trip():
    state = golden_state()
    state.map = MapState(
        start_room="hall",
        rooms={
            "hall": Room(name="입구 홀", exits={"down": Exit(to_room="cellar", label="계단")}),
            "cellar": Room(name="지하실"),
        },
    )
    state.actors["player"].location = "hall"
    state.actors["player"].visited_rooms = {"hall", "cellar"}
    dumped = state.model_dump(mode="json")
    assert dumped["map"]["rooms"]["hall"]["exits"]["down"]["to_room"] == "cellar"
    assert dumped["actors"]["player"]["location"] == "hall"
    assert dumped["actors"]["player"]["visited_rooms"] == ["cellar", "hall"]
    assert "location" not in dumped["actors"]["opponent"]
    assert WorldState.model_validate_json(state.model_dump_json()) == state


def test_clearing_a_placement_and_map_restores_the_golden_hash():
    state = golden_state()
    state.map = MapState(start_room="hall", rooms={"hall": Room(name="입구 홀")})
    placed = state.actors["player"].model_copy(
        update={"location": "hall", "visited_rooms": {"hall"}}
    )
    state.actors["player"] = placed.model_copy(update={"location": None, "visited_rooms": set()})
    state.map = None
    assert stable_model_hash(state) == GOLDEN_STATE_HASH
    assert hashlib.sha256(state.model_dump_json().encode()).hexdigest() == GOLDEN_JSON_SHA256


def test_the_documented_schema_keeps_every_field():
    # FastAPI documents responses with the serialization schema; it must stay structured.
    schema = WorldState.model_json_schema(mode="serialization")
    assert "map" in schema["properties"]
    actor = schema["$defs"]["ActorState"]["properties"]
    assert {"location", "visited_rooms", "conditions", "hp"} <= set(actor)
