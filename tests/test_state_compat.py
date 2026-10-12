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
