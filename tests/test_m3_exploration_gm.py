"""M3 invariants: Map v2 (locked exits, loot, clues, NPC move), quests, A3 GM correction, B1/B2."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_risu_api import CountingDice, setup

from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.domain.events import (
    GM_CORRECTION_KIND,
    INSPECT_ROOM_KIND,
    MOVE_NPC_KIND,
    QUEST_UPDATE_KIND,
    TAKE_LOOT_KIND,
    UNLOCK_EXIT_KIND,
    GameEvent,
)
from saga_trpg.domain.models import Exit, MapState, Room, WorldState
from saga_trpg.runtime.hashing import stable_model_hash

OUTPOST_TEXT = Path("packs/scenarios/black-tower-outpost/scenario.json").read_text(encoding="utf-8")


def test_map_v2_fields_are_hash_neutral_when_default():
    plain_exit = Exit(to_room="b", label="문", kind="door")
    assert plain_exit.model_dump(mode="json") == {"to_room": "b", "label": "문", "kind": "door"}
    plain_room = Room(name="홀", description="넓은 홀", exits={"north": plain_exit})
    assert plain_room.model_dump(mode="json") == {
        "name": "홀",
        "description": "넓은 홀",
        "exits": {"north": {"to_room": "b", "label": "문", "kind": "door"}},
    }
    w1 = WorldState(
        world_id="w-neutral",
        map=MapState(start_room="a", rooms={"a": plain_room}),
    )
    assert "locked" not in w1.model_dump_json()
    assert "loot" not in w1.model_dump_json()
    assert "clues" not in w1.model_dump_json()
    assert stable_model_hash(w1) == stable_model_hash(
        WorldState.model_validate_json(w1.model_dump_json())
    )


def test_map_v2_exploration_loop_locked_door_loot_clues_npc_move_and_quests(tmp_path):
    repo, dice, intent, client = setup(tmp_path)

    def hook(actions, *, proposal=None, scenario=None):
        body: dict = {
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-m3-explore",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": mid, "text": txt} for mid, txt in actions],
            }
        }
        if scenario is not None:
            body["saga"]["scenario"] = {"content": scenario}
        if proposal is not None:
            body["client_proposal"] = proposal
        return client.post("/v1/saga/turn", json=body)

    # 1. Start in courtyard: undiscovered clue hidden, cellar door locked, raider hidden in cellar.
    a = [("u1", "안뜰에 도착해 주변을 본다")]
    r1 = hook(a, scenario=OUTPOST_TEXT)
    assert r1.status_code == 200, r1.text
    s1 = r1.json()["saga"]
    assert "군화 자국" not in s1["prompt_block"]
    assert s1["scene_board"]["current_room"] == "전초기지 안뜰"
    assert s1["scene_board"]["room_loot"] == {"healing_herb": 1}
    assert s1["scene_board"]["discovered_clues"] == {}
    # B2: only player is in courtyard; captain_sera (watchtower) & raider (cellar) stay hidden!
    assert [p["actor_id"] for p in s1["scene_board"]["participants"]] == ["player"]
    down_exit = next(e for e in s1["scene_board"]["exits"] if e["key"] == "down")
    assert down_exit["locked"] is True and down_exit["destination"] == "???"

    # 2. Moving through locked cellar door is refused (422) without mutation!
    locked_try = hook(a + [("u2", "move down")])
    assert locked_try.status_code == 422
    assert "locked" in locked_try.json()["detail"]

    # 3. Unlocking without silver_key is refused (422)!
    no_key_try = hook(
        a + [("u2", "지하 문을 연다")],
        proposal={"kind": UNLOCK_EXIT_KIND, "payload": {"exit": "down"}},
    )
    assert no_key_try.status_code == 422
    assert "silver_key" in no_key_try.json()["detail"]

    # 4. Inspect courtyard -> discovers hidden clue; persists afterwards.
    a.append(("u2", "안뜰 바닥을 자세히 조사한다"))
    r2 = hook(a, proposal={"kind": INSPECT_ROOM_KIND, "payload": {}})
    assert r2.status_code == 200, r2.text
    s2 = r2.json()["saga"]
    assert "bootprints" in s2["scene_board"]["discovered_clues"]
    assert "군화 자국" in s2["prompt_block"]

    # 5. Take finite loot (healing_herb) once -> second claim fails (no duplicate loot!).
    a.append(("u3", "약초를 줍는다"))
    r3 = hook(
        a, proposal={"kind": TAKE_LOOT_KIND, "payload": {"item": "healing_herb", "amount": 1}}
    )
    assert r3.status_code == 200, r3.text
    s3 = r3.json()["saga"]
    assert s3["state"]["actors"]["player"]["inventory"]["healing_herb"] == 1
    assert s3["scene_board"]["room_loot"] == {}
    assert any("healing_herb" in c for c in s3["turn_card"]["changes"])

    dup_loot = hook(
        a + [("u4", "약초를 또 줍는다")],
        proposal={"kind": TAKE_LOOT_KIND, "payload": {"item": "healing_herb", "amount": 1}},
    )
    assert dup_loot.status_code == 422

    # 6. Move north to watchtower -> captain_sera now appears on B2 scene board, take silver_key!
    a.append(("u4", "move north"))
    r4 = hook(a)
    assert r4.status_code == 200, r4.text
    s4 = r4.json()["saga"]
    assert [p["actor_id"] for p in s4["scene_board"]["participants"]] == ["player", "captain_sera"]

    a.append(("u5", "은열쇠를 챙긴다"))
    r5 = hook(a, proposal={"kind": TAKE_LOOT_KIND, "payload": {"item": "silver_key", "amount": 1}})
    assert r5.status_code == 200, r5.text

    # 7. Return south, unlock down with silver_key, move down into cellar -> raider now visible!
    a.append(("u6", "move south"))
    assert hook(a).status_code == 200

    a.append(("u7", "은열쇠로 지하 철문을 연다"))
    r7 = hook(a, proposal={"kind": UNLOCK_EXIT_KIND, "payload": {"exit": "down"}})
    assert r7.status_code == 200, r7.text
    assert any(".locked: True → False" in c for c in r7.json()["saga"]["turn_card"]["changes"])

    a.append(("u8", "move down"))
    r8 = hook(a)
    assert r8.status_code == 200, r8.text
    s8 = r8.json()["saga"]
    assert s8["scene_board"]["current_room"] == "지하 보급 창고"
    assert [p["actor_id"] for p in s8["scene_board"]["participants"]] == ["player", "raider"]

    # 8. Move NPC captain_sera south to courtyard & update quest stage!
    a.append(("u9", "세라 대장이 안뜰로 내려온다"))
    r9 = hook(
        a, proposal={"kind": MOVE_NPC_KIND, "payload": {"npc_id": "captain_sera", "exit": "south"}}
    )
    assert r9.status_code == 200, r9.text
    assert r9.json()["saga"]["state"]["actors"]["captain_sera"]["location"] == "courtyard"

    a.append(("u10", "퀘스트를 갱신한다"))
    r10 = hook(
        a,
        proposal={
            "kind": QUEST_UPDATE_KIND,
            "payload": {
                "quest_id": "black_tower",
                "stage": 2,
                "status": "active",
                "objective": "Confront the raider in the cellar",
                "note": "Unlocked cellar with silver key",
            },
        },
    )
    assert r10.status_code == 200, r10.text
    s10 = r10.json()["saga"]
    assert s10["scene_board"]["quests"][0]["stage"] == 2
    assert repo.replay(s10["world_id"]).model_dump(mode="json") == s10["state"]


def test_gm_correction_is_append_only_auditable_and_replays_after_restart(tmp_path):
    db_path = tmp_path / "m3-gm.sqlite3"
    repo = SQLiteWorldRepository(db_path, dice=CountingDice())
    srd = repo.registry.get("srd-5.2.1", "0.6.0")
    repo.create_world(srd.example_world("w-gm"), ruleset_id="srd-5.2.1", ruleset_version="0.6.0")

    e1 = repo.dispatch(
        "w-gm",
        GameEvent(
            event_id="ev-1",
            kind="attack",
            actor_id="player",
            payload={"target_id": "opponent", "advantage": False, "disadvantage": False},
        ),
    ).entry
    e1_snapshot = e1.model_dump(mode="json")

    # Reject empty gm_correction or negative inventory
    with pytest.raises(ValueError, match="at least one"):
        repo.dispatch(
            "w-gm",
            GameEvent(
                event_id="ev-bad",
                kind=GM_CORRECTION_KIND,
                actor_id="player",
                payload={"reason": "oops"},
            ),
        )
    with pytest.raises(ValueError, match="negative"):
        repo.dispatch(
            "w-gm",
            GameEvent(
                event_id="ev-neg",
                kind=GM_CORRECTION_KIND,
                actor_id="player",
                payload={"reason": "take missing", "inventory_changes": {"player": {"gold": -5}}},
            ),
        )

    # Apply explicit append-only GM correction referencing ev-1
    corr = repo.dispatch(
        "w-gm",
        GameEvent(
            event_id="ev-2",
            kind=GM_CORRECTION_KIND,
            actor_id="player",
            payload={
                "reason": "누락된 전투 보상 지급 및 HP 정산",
                "target_event_id": "ev-1",
                "hp_changes": {"player": -3, "opponent": -100},
                "inventory_changes": {"player": {"silver_coin": 15}},
                "add_conditions": {"opponent": ["unconscious"]},
                "quest_updates": {"bounty": {"status": "completed", "stage": 1}},
            },
        ),
    )
    assert corr.replayed is False
    state = corr.entry.result.state
    assert state.actors["player"].hp == 17
    assert state.actors["opponent"].hp == 0  # clamped to 0
    assert state.actors["player"].inventory["silver_coin"] == 15
    assert "unconscious" in state.actors["opponent"].conditions
    assert state.quest_state["bounty"]["status"] == "completed"
    assert "ev-1" in corr.entry.result.facts[0]
    # Historical entry ev-1 was NEVER mutated in place!
    assert repo.get_event("w-gm", "ev-1").model_dump(mode="json") == e1_snapshot

    # Restart & replay reproduces exact corrected state
    restarted = SQLiteWorldRepository(db_path, dice=CountingDice())
    assert restarted.replay("w-gm") == state
    assert len(restarted.entries("w-gm")) == 2
