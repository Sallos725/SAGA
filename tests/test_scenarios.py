"""M2 Slice 1 invariants: scenario cards separated from rulebooks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_risu_api import setup

from saga_trpg.rulebooks.__main__ import main as cli_main
from saga_trpg.rulebooks.loader import load_rulebook, rulebook_path
from saga_trpg.rulebooks.scenarios import (
    ScenarioError,
    build_scenario_module,
    build_scenario_world,
    load_scenario,
    parse_scenario,
)

OUTPOST_PATH = Path("packs/scenarios/black-tower-outpost/scenario.json")


def test_bundled_black_tower_outpost_scenario_validates_and_builds_world():
    srd = load_rulebook(rulebook_path("srd-5.2.1"))
    scenario = load_scenario(OUTPOST_PATH)
    world = build_scenario_world(scenario, "w-outpost", rulebook=srd)

    assert world.world_id == "w-outpost"
    assert set(world.actors) == {"player", "captain_sera", "raider"}
    assert world.actors["player"].location == "courtyard"
    assert world.actors["captain_sera"].location == "watchtower"
    assert world.actors["raider"].location == "cellar"
    assert world.actors["player"].visited_rooms == {"courtyard"}
    assert world.quest_state["black_tower"]["stage"] == 1
    assert world.flags["weather"] == "rain"
    assert world.flags["_scenario"] == {"id": "black-tower-outpost", "version": "0.1.0"}


def test_scenario_rejects_mismatched_ruleset_bad_map_and_unknown_conditions():
    fate = load_rulebook(rulebook_path("fate-condensed"))
    srd = load_rulebook(rulebook_path("srd-5.2.1"))
    scenario = load_scenario(OUTPOST_PATH)
    with pytest.raises(ScenarioError, match="ruleset_id"):
        build_scenario_world(scenario, "w-wrong", rulebook=fate)

    bad_map = json.loads(OUTPOST_PATH.read_text(encoding="utf-8"))
    bad_map["map"]["rooms"]["courtyard"]["exits"]["north"]["to_room"] = "nonexistent_room"
    with pytest.raises(ScenarioError, match="unknown room"):
        parse_scenario(json.dumps(bad_map))

    bad_cond = json.loads(OUTPOST_PATH.read_text(encoding="utf-8"))
    bad_cond["actors"]["player"]["conditions"] = ["imaginary_curse"]
    with pytest.raises(ScenarioError, match="unknown condition"):
        build_scenario_world(parse_scenario(json.dumps(bad_cond)), "w-cond", rulebook=srd)


def test_scenario_seeded_map_generation_is_deterministic():
    srd = load_rulebook(rulebook_path("srd-5.2.1"))
    raw = {
        "schema_version": 1,
        "id": "proc-crypt",
        "version": "0.1.0",
        "title": "Procedural Crypt",
        "actors": {"player": {"hp": 20, "max_hp": 20}},
        "map": {"generate": {"seed": "crypt-alpha", "rooms": [5, 7]}},
    }
    sc = parse_scenario(json.dumps(raw))
    w1 = build_scenario_world(sc, "world-abc", rulebook=srd)
    w2 = build_scenario_world(sc, "world-abc", rulebook=srd)
    assert w1.model_dump() == w2.model_dump()
    assert w1.map is not None and 5 <= len(w1.map.rooms) <= 7


def test_http_create_world_and_hook_turn_accept_scenario_and_pin_on_creation(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    scenario_text = OUTPOST_PATH.read_text(encoding="utf-8")

    # 1. Direct HTTP POST /worlds/{id} with scenario
    created = client.post(
        "/worlds/outpost-http",
        json={"ruleset_id": "srd-5.2.1", "ruleset_version": "0.6.0", "scenario": scenario_text},
    )
    assert created.status_code == 201, created.text
    state = created.json()
    assert set(state["actors"]) == {"player", "captain_sera", "raider"}
    assert state["actors"]["player"]["location"] == "courtyard"

    # 2. Hook mode /v1/saga/turn with scenario attachment on a new chat
    turn1 = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-outpost",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": "u1", "text": "안뜰에서 비를 피하며 주변을 살핀다."}],
                "scenario": {"content": scenario_text},
            }
        },
    )
    assert turn1.status_code == 200, turn1.text
    s1 = turn1.json()["saga"]
    assert s1["state"]["actors"]["player"]["location"] == "courtyard"
    # Unseen cellar & raider inside it stay hidden behind door ??? exit in prompt_block!
    assert "전초기지 안뜰" in s1["prompt_block"]
    assert "지하 보급 창고" not in s1["prompt_block"]
    assert "cellar" not in s1["prompt_block"]

    # Turn 2: move north to watchtower, passing a *different* scenario is ignored (world pinned!)
    other_scenario = json.dumps(
        {
            "schema_version": 1,
            "id": "other",
            "version": "0.1.0",
            "title": "Other",
            "actors": {"player": {"hp": 99, "max_hp": 99}},
        }
    )
    turn2 = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-outpost",
                "purpose": "action",
                "actor_id": "player",
                "actions": [
                    {"message_id": "u1", "text": "안뜰에서 비를 피하며 주변을 살핀다."},
                    {"message_id": "u2", "text": "move north"},
                ],
                "scenario": {"content": other_scenario},
            }
        },
    )
    assert turn2.status_code == 200, turn2.text
    s2 = turn2.json()["saga"]
    assert s2["state"]["actors"]["player"]["location"] == "watchtower"
    assert s2["state"]["actors"]["player"]["hp"] == 20
    assert "북쪽 망루" in s2["prompt_block"]
    assert "captain_sera" in s2["prompt_block"]
    assert repo.replay(s2["world_id"]).model_dump(mode="json") == s2["state"]


def test_scenario_cli_check_and_risu_module_export(tmp_path):
    assert (
        cli_main(
            [
                "check-scenario",
                str(OUTPOST_PATH),
                "--rulebook",
                str(rulebook_path("srd-5.2.1")),
            ]
        )
        == 0
    )
    out = tmp_path / "outpost.module.json"
    assert cli_main(["scenario-module", str(OUTPOST_PATH), "--out", str(out)]) == 0
    exported = json.loads(out.read_text(encoding="utf-8"))
    assert exported["type"] == "risuModule"
    entries = exported["module"]["lorebook"]
    assert any(e["comment"].startswith("saga:scenario:black-tower-outpost@0.1.0:") for e in entries)
    data_entry = next(e for e in entries if e["comment"].startswith("saga:scenario:"))
    assert data_entry["key"] == "(?!)" and data_entry["alwaysActive"] is False
    assert build_scenario_module(OUTPOST_PATH.read_text(encoding="utf-8")) == exported
