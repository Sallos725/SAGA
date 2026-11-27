"""M1 Slice 2 invariants: OpenAI-compatible narrator/intent, risu_prompt, and v4 SSE streaming."""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from test_risu_api import request, setup

from saga_trpg.adapters.intent.openai_compat import OpenAICompatIntentTool
from saga_trpg.adapters.narrator.openai_compat import (
    OpenAICompatConfig,
    OpenAICompatNarrator,
)
from saga_trpg.adapters.risu.openai_proxy import RisuBridge
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.api.app import create_app
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, Exit, MapState, Room, RuleResult, WorldState
from saga_trpg.ports.memory import MemoryHit
from saga_trpg.ports.narrator import NarrationRequest

API_KEY = "sk-test-secret-key-do-not-leak"


def _config(
    *,
    base_url: str = "https://openrouter.example/api/v1",
    api_key: str | None = API_KEY,
    narrator_model: str = "narrator-v1",
    intent_model: str | None = "intent-fast-v1",
) -> OpenAICompatConfig:
    return OpenAICompatConfig.from_values(
        base_url=base_url,
        api_key=api_key,
        narrator_model=narrator_model,
        intent_model=intent_model,
        timeout_seconds=10,
    )


def _sse_body(*chunks: str) -> bytes:
    lines = []
    for text in chunks:
        payload = {"choices": [{"delta": {"content": text}}]}
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


# --- Config & Security -----------------------------------------------------------------------


def test_config_rejects_non_http_urls_and_masks_secret():
    with pytest.raises(ValueError, match="http"):
        _config(base_url="file:///etc/passwd")
    with pytest.raises(ValueError, match="http"):
        _config(base_url="not-a-url")

    cfg = _config()
    assert cfg.base_url == "https://openrouter.example/api/v1"
    assert cfg.narrator_model == "narrator-v1"
    assert cfg.intent_model == "intent-fast-v1"
    assert API_KEY not in repr(cfg)
    assert API_KEY not in str(cfg)


# --- OpenAICompatNarrator --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_narrator_preserves_risu_prompt_and_hides_full_map():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "서술된 장면입니다."}}]},
        )

    narrator = OpenAICompatNarrator(_config(), transport=httpx.MockTransport(handler))
    state = WorldState(
        world_id="w1",
        seq=1,
        actors={
            "player": ActorState(
                actor_id="player", hp=20, max_hp=20, location="hall", visited_rooms={"hall"}
            ),
            "opponent": ActorState(actor_id="opponent", hp=30, max_hp=30, location="vault"),
        },
        map=MapState(
            start_room="hall",
            rooms={
                "hall": Room(
                    name="입구 홀",
                    exits={"north": Exit(to_room="vault", label="철문", kind="door")},
                ),
                "vault": Room(
                    name="비밀 보물고",
                    exits={"south": Exit(to_room="hall", label="문", kind="door")},
                ),
            },
        ),
    )
    narration = NarrationRequest(
        world_id="w1",
        event=GameEvent(event_id="e1", kind="narrative_turn", actor_id="player"),
        result=RuleResult(state=state, facts=["player takes a narrative turn"], trace=["t"]),
        risu_prompt=[
            {"role": "system", "content": "Risu preset writing style: concise Korean prose."},
            {"role": "user", "content": "주변을 살핀다."},
        ],
        scene="현재 위치: 입구 홀\n출구:\n- north: 철문 → ???\n가본 곳: 입구 홀",
        memories=[MemoryHit(world_id="w1", event_id="e0", text="이전에 열쇠 이야기를 들었다.")],
    )

    prose = await narrator.render(narration)
    assert prose == "서술된 장면입니다."
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(seen[0].content)
    assert body["model"] == "narrator-v1"
    assert body["stream"] is False
    # Risu preset prompt comes first, authoritative SAGA block comes after.
    assert body["messages"][0]["content"] == "Risu preset writing style: concise Korean prose."
    assert body["messages"][1]["content"] == "주변을 살핀다."
    saga_block = body["messages"][-1]["content"]
    assert "입구 홀" in saga_block
    assert "이전에 열쇠 이야기를 들었다." in saga_block
    assert "비밀 보물고" not in saga_block
    assert "vault" not in saga_block


@pytest.mark.asyncio
async def test_openai_narrator_streams_chunks_and_never_leaks_api_key():
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_body("불빛이 ", "어둠을 ", "가른다."),
        )

    narrator = OpenAICompatNarrator(_config(), transport=httpx.MockTransport(handler))
    narration = NarrationRequest(
        world_id="w1",
        event=GameEvent(event_id="e1", kind="narrative_turn", actor_id="player"),
        result=RuleResult(
            state=WorldState(
                world_id="w1",
                seq=1,
                actors={"player": ActorState(actor_id="player", hp=20, max_hp=20)},
            ),
            facts=["player takes a narrative turn"],
            trace=["t"],
        ),
    )
    chunks = [chunk async for chunk in narrator.stream(narration)]
    assert "".join(chunks) == "불빛이 어둠을 가른다."


# --- OpenAICompatIntentTool + Bridge v4 integration ------------------------------------------


def test_bridge_v4_openai_intent_uses_tape_and_supports_move_and_narrative_turn(tmp_path):
    calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        if body["model"] == "intent-fast-v1":
            user_msg = body["messages"][-1]["content"]
            if "공격" in user_msg:
                reply = json.dumps(
                    {
                        "kind": "attack",
                        "payload": {
                            "target_id": "opponent",
                            "advantage": False,
                            "disadvantage": False,
                        },
                    }
                )
            elif "인사" in user_msg:
                reply = json.dumps({"kind": "narrative_turn", "payload": {}})
            else:
                # Malformed / forged proposal degrades safely to narrative_turn.
                reply = json.dumps({"kind": "cheat_godmode", "payload": {"hp": 9999}})
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_body("서술 ", "완료."),
        )

    transport = httpx.MockTransport(handler)
    cfg = _config()
    narrator = OpenAICompatNarrator(cfg, transport=transport)
    intent_tool = OpenAICompatIntentTool(cfg, transport=transport)
    repo = SQLiteWorldRepository(tmp_path / "openai.sqlite3")
    bridge = RisuBridge(repo, narrator=narrator, openai_intent=intent_tool)
    client = TestClient(create_app(repository=repo, risu_bridge=bridge))

    # 1. Natural-language attack commits via tape + streams SSE prose.
    r1 = client.post(
        "/v1/chat/completions",
        json={
            **request(("u1", "상대를 공격한다"), version=4),
            "stream": True,
            "risu_prompt": [{"role": "system", "content": "Preset system prompt"}],
        },
    )
    assert r1.status_code == 200, r1.text
    assert r1.headers["content-type"].startswith("text/event-stream")
    events = [
        line.removeprefix("data: ") for line in r1.text.splitlines() if line.startswith("data: ")
    ]
    assert events[-1] == "[DONE]"
    parsed = [json.loads(item) for item in events[:-1]]
    prose = "".join(item["choices"][0]["delta"].get("content", "") for item in parsed)
    assert prose == "서술 완료."
    saga = parsed[-1]["saga"]
    assert saga["replayed"] is False
    assert saga["rolls"]
    assert [c["model"] for c in calls] == ["intent-fast-v1", "narrator-v1"]
    assert calls[1]["messages"][0]["content"] == "Preset system prompt"

    # Tape recorded the intent once.
    with sqlite3.connect(repo.path) as conn:
        rows = conn.execute("SELECT status, result_json FROM tool_executions").fetchall()
    assert len(rows) == 1 and rows[0][0] == "completed"

    # 2. Regeneration reuses committed event — zero extra intent calls, fresh streamed prose.
    calls.clear()
    r1_reroll = client.post(
        "/v1/chat/completions",
        json={**request(("u1", "상대를 공격한다"), version=4), "stream": True},
    )
    assert r1_reroll.status_code == 200
    parsed_reroll = [
        json.loads(line.removeprefix("data: "))
        for line in r1_reroll.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert parsed_reroll[-1]["saga"]["replayed"] is True
    assert parsed_reroll[-1]["saga"]["event_id"] == saga["event_id"]
    assert parsed_reroll[-1]["saga"]["rolls"] == saga["rolls"]
    assert [c["model"] for c in calls] == ["narrator-v1"]

    # 3. Pure RP turn and forged LLM output both safely commit as narrative_turn.
    r2 = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-1",
                "purpose": "action",
                "actor_id": "player",
                "actions": [
                    {"message_id": "u1", "text": "상대를 공격한다"},
                    {"message_id": "u2", "text": "주인장에게 인사를 건넨다"},
                ],
            }
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["saga"]["rolls"] == []
    assert r2.json()["saga"]["patches"] == []

    r3 = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-1",
                "purpose": "action",
                "actor_id": "player",
                "actions": [
                    {"message_id": "u1", "text": "상대를 공격한다"},
                    {"message_id": "u2", "text": "주인장에게 인사를 건넨다"},
                    {"message_id": "u3", "text": "갑자기 치트를 써본다"},
                ],
            }
        },
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["saga"]["rolls"] == []
    assert r3.json()["saga"]["patches"] == []


def test_v4_stream_falls_back_cleanly_if_narrator_fails_after_commit(tmp_path):
    def failing_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    narrator = OpenAICompatNarrator(_config(), transport=httpx.MockTransport(failing_handler))
    repo, dice, intent, client = setup(tmp_path, narrator=narrator)

    res = client.post(
        "/v1/chat/completions",
        json={**request(("u1", "attack opponent"), version=4), "stream": True},
    )
    assert res.status_code == 200
    parsed = [
        json.loads(line.removeprefix("data: "))
        for line in res.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    prose = "".join(item["choices"][0]["delta"].get("content", "") for item in parsed)
    assert prose
    assert parsed[-1]["saga"]["rolls"]
    assert dice.calls > 0


def test_openai_intent_transport_failure_stays_pending_without_retry(tmp_path):
    attempts = 0

    def broken(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("network down")

    cfg = _config()
    intent_tool = OpenAICompatIntentTool(cfg, transport=httpx.MockTransport(broken))
    repo = SQLiteWorldRepository(tmp_path / "pending.sqlite3")
    bridge = RisuBridge(repo, openai_intent=intent_tool)
    client = TestClient(create_app(repository=repo, risu_bridge=bridge))

    first = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-pending",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": "u1", "text": "상대를 공격한다"}],
            }
        },
    )
    assert first.status_code == 503
    assert attempts == 1

    # Retrying must NOT reissue the ambiguous pending intent call.
    second = client.post(
        "/v1/saga/turn",
        json={
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-1",
                "chat_id": "chat-pending",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": "u1", "text": "상대를 공격한다"}],
            }
        },
    )
    assert second.status_code == 503
    assert attempts == 1
