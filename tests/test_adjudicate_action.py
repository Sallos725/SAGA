"""M2 Slice 2 & 3 invariants: grounded NL movement/actions and A1 free-action adjudication."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_risu_api import CountingDice, setup

from saga_trpg.adapters.intent.openai_compat import OpenAICompatIntentTool
from saga_trpg.adapters.narrator.openai_compat import OpenAICompatConfig
from saga_trpg.adapters.risu.openai_proxy import RisuBridge
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.api.app import create_app
from saga_trpg.domain.events import ADJUDICATE_ACTION_KIND, GameEvent
from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from saga_trpg.runtime.engine import RuntimeEngine
from saga_trpg.runtime.journal import InMemoryJournal
from saga_trpg.runtime.replay import replay_recorded_results

OUTPOST_TEXT = Path("packs/scenarios/black-tower-outpost/scenario.json").read_text(encoding="utf-8")


# --- Runtime A1 adjudicate_action invariants --------------------------------------------------


def test_adjudicate_action_check_auto_success_and_impossible_are_deterministic_and_replayable():
    dice = CountingDice()
    initial = WorldState(
        world_id="w-a1",
        actors={"player": ActorState(actor_id="player", hp=20, max_hp=20)},
    )
    engine = RuntimeEngine(
        initial_state=initial,
        rules=GenericFantasyRules(),
        dice=dice,
        journal=InMemoryJournal(),
    )

    # 1. Check with advantage (rolls 2 d20s, zero direct state patches)
    check_event = GameEvent(
        event_id="e1",
        kind=ADJUDICATE_ACTION_KIND,
        actor_id="player",
        payload={
            "description": "밧줄을 잘라 샹들리에를 떨어뜨린다",
            "mode": "check",
            "difficulty": 12,
            "modifier": 3,
            "advantage": True,
            "ability": "dexterity",
            "stakes": "성공 시 적이 넘어짐, 실패 시 밧줄이 빗나감",
        },
    )
    res1 = engine.dispatch(check_event)
    assert res1.replayed is False
    assert len(res1.entry.result.rolls) == 2
    assert res1.entry.result.patches == []
    assert res1.entry.result.resolution is not None
    assert res1.entry.result.resolution.target == 12
    best = max(r.total for r in res1.entry.result.rolls)
    assert res1.entry.result.resolution.total == best + 3
    assert res1.entry.result.resolution.outcome == ("success" if best + 3 >= 12 else "failure")
    rolls_after_check = dice.calls

    # Retrying the same event replays without re-rolling
    res1_retry = engine.dispatch(check_event)
    assert res1_retry.replayed is True
    assert dice.calls == rolls_after_check

    # 2. Auto-success (zero rolls)
    res2 = engine.dispatch(
        GameEvent(
            event_id="e2",
            kind=ADJUDICATE_ACTION_KIND,
            actor_id="player",
            payload={
                "description": "열려 있는 나무 상자를 열어본다",
                "mode": "auto_success",
                "reason": "잠겨 있지 않고 시간 압박이 없음",
            },
        )
    )
    assert res2.entry.result.rolls == []
    assert res2.entry.result.resolution is None
    assert dice.calls == rolls_after_check

    # 3. Impossible (zero rolls, explains why)
    res3 = engine.dispatch(
        GameEvent(
            event_id="e3",
            kind=ADJUDICATE_ACTION_KIND,
            actor_id="player",
            payload={
                "description": "맨손으로 성벽을 부순다",
                "mode": "impossible",
                "reason": "두꺼운 석조 성벽은 맨손으로 파괴할 수 없음",
            },
        )
    )
    assert res3.entry.result.rolls == []
    assert "cannot perform" in res3.entry.result.facts[0]
    assert dice.calls == rolls_after_check

    # Replay from genesis reproduces final state exactly
    rebuilt = replay_recorded_results(initial, engine.journal.entries())
    assert rebuilt == engine.state
    assert engine.state.actors["player"].hp == 20


def test_adjudicate_action_rejects_invalid_mode_combinations_and_extra_fields():
    engine = RuntimeEngine(
        initial_state=WorldState(
            world_id="w-a1-bad",
            actors={"player": ActorState(actor_id="player", hp=20, max_hp=20)},
        ),
        rules=GenericFantasyRules(),
        dice=CountingDice(),
        journal=InMemoryJournal(),
    )
    bad_payloads = [
        {"description": "no dc", "mode": "check"},
        {"description": "dc on impossible", "mode": "impossible", "difficulty": 10},
        {"description": "adv on auto", "mode": "auto_success", "advantage": True},
        {
            "description": "adv on 2d6",
            "mode": "check",
            "difficulty": 8,
            "dice_expression": "2d6",
            "advantage": True,
        },
        {"description": "extra field", "mode": "auto_success", "hp_delta": 50},
    ]
    for payload in bad_payloads:
        with pytest.raises(ValueError):
            engine.dispatch(
                GameEvent(kind=ADJUDICATE_ACTION_KIND, actor_id="player", payload=payload)
            )
    assert engine.journal.entries() == []


# --- Grounded NL movement & A1 adjudication through /v1/saga/turn and durable tape -----------


def test_hook_client_proposal_grounds_move_exits_and_adjudicates_free_action(tmp_path):
    repo, dice, intent, client = setup(tmp_path)

    # Hallucinated exit ('east' does not exist in courtyard) is rejected with 422 before creation.
    bad_move = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-a1",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": "u1", "text": "동쪽으로 간다"}],
                "scenario": {"content": OUTPOST_TEXT},
            },
            "client_proposal": {"kind": "move", "payload": {"exit": "east"}},
        },
    )
    assert bad_move.status_code == 422
    assert "no exit 'east'" in bad_move.json()["detail"]

    # Valid NL movement ('north' exists in courtyard) moves player to watchtower!
    good_move = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-a1",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": "u1", "text": "북쪽 망루로 올라간다"}],
                "scenario": {"content": OUTPOST_TEXT},
            },
            "client_proposal": {"kind": "move", "payload": {"exit": "north"}},
        },
    )
    assert good_move.status_code == 200, good_move.text
    s1 = good_move.json()["saga"]
    assert s1["state"]["actors"]["player"]["location"] == "watchtower"

    # Turn 2: creative free action adjudicated via A1 check
    free_req = {
        "saga": {
            "schema_version": 4,
            "host_instance_id": "host-1",
            "chat_id": "chat-a1",
            "purpose": "action",
            "actor_id": "player",
            "actions": [
                {"message_id": "u1", "text": "북쪽 망루로 올라간다"},
                {"message_id": "u2", "text": "난간 밧줄을 묶어 아래를 내려다본다"},
            ],
        },
        "client_proposal": {
            "kind": "adjudicate_action",
            "payload": {
                "description": "난간에 밧줄을 단단히 고정하고 아래를 정찰한다",
                "mode": "check",
                "difficulty": 11,
                "modifier": 2,
                "ability": "dexterity",
                "stakes": "성공 시 안뜰과 지하문 주변 시야 확보",
            },
        },
    }
    t2 = client.post("/v1/saga/turn", json=free_req)
    assert t2.status_code == 200, t2.text
    s2 = t2.json()["saga"]
    assert len(s2["rolls"]) == 1
    assert s2["resolution"]["target"] == 11
    assert "난간에 밧줄을 단단히 고정" in s2["prompt_block"]
    rolls_before = dice.calls

    # Regenerating with a lower DC attempt ignores the new proposal and reuses the committed roll!
    tampered = json.loads(json.dumps(free_req))
    tampered["client_proposal"]["payload"]["difficulty"] = 1
    t2_reroll = client.post("/v1/saga/turn", json=tampered)
    assert t2_reroll.status_code == 200
    assert t2_reroll.json()["saga"]["replayed"] is True
    assert t2_reroll.json()["saga"]["resolution"] == s2["resolution"]
    assert dice.calls == rolls_before


def test_openai_intent_tape_classifies_nl_move_and_free_action_without_leaking_unseen_map(
    tmp_path,
):
    seen_inputs: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        arg = json.loads(body["messages"][-1]["content"])
        seen_inputs.append(arg)
        if "북쪽" in arg["text"]:
            reply = {"kind": "move", "payload": {"exit": "north"}}
        else:
            reply = {
                "kind": "adjudicate_action",
                "payload": {
                    "description": "횃불을 던져 안개 너머를 비춘다",
                    "mode": "check",
                    "difficulty": 13,
                    "modifier": 1,
                    "ability": "wisdom",
                },
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    cfg = OpenAICompatConfig.from_values(
        base_url="https://llm.example/v1",
        narrator_model="n1",
        intent_model="i1",
        api_key="sk-secret",
    )
    intent_tool = OpenAICompatIntentTool(cfg, transport=httpx.MockTransport(handler))
    repo = SQLiteWorldRepository(tmp_path / "m2-intent.sqlite3")
    client = TestClient(
        create_app(repository=repo, risu_bridge=RisuBridge(repo, openai_intent=intent_tool))
    )

    r1 = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-nl-move",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": "u1", "text": "북쪽 계단으로 올라간다"}],
                "scenario": {"content": OUTPOST_TEXT},
            }
        },
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["saga"]["state"]["actors"]["player"]["location"] == "watchtower"
    # Intent prompt saw courtyard exits (with hints), never the hidden cellar room name!
    assert seen_inputs[0]["current_room"] == "전초기지 안뜰"
    assert "지하 보급 창고" not in json.dumps(seen_inputs[0], ensure_ascii=False)
    assert "cellar" not in json.dumps(seen_inputs[0], ensure_ascii=False)

    r2 = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-nl-move",
                "purpose": "action",
                "actor_id": "player",
                "actions": [
                    {"message_id": "u1", "text": "북쪽 계단으로 올라간다"},
                    {"message_id": "u2", "text": "횃불을 멀리 던져 어둠을 살핀다"},
                ],
            }
        },
    )
    assert r2.status_code == 200, r2.text
    s2 = r2.json()["saga"]
    assert s2["resolution"]["target"] == 13
    assert len(s2["rolls"]) == 1

    # Durable tape recorded both interpretations once; reroll triggers zero extra calls.
    with sqlite3.connect(repo.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tool_executions").fetchone()[0] == 2
    r2_again = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-nl-move",
                "purpose": "action",
                "actor_id": "player",
                "actions": [
                    {"message_id": "u1", "text": "북쪽 계단으로 올라간다"},
                    {"message_id": "u2", "text": "횃불을 멀리 던져 어둠을 살핀다"},
                ],
            }
        },
    )
    assert r2_again.status_code == 200 and r2_again.json()["saga"]["replayed"] is True
    assert len(seen_inputs) == 2
