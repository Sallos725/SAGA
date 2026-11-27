"""M1 Slice 1 invariants: narrative_turn no-op events, protocol v4, and Hook-mode /v1/saga/turn."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_risu_api import CountingDice, request, setup

from saga_trpg.adapters.risu.openai_proxy import NARRATIVE_TURN_KIND, TurnRequest
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, Exit, MapState, Room, WorldState
from saga_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from saga_trpg.modules.srd_5_2_1.rules import SRD521Rules
from saga_trpg.ports.narrator import NarrationRequest
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import load_rulebook, rulebook_path
from saga_trpg.runtime.engine import RuntimeEngine
from saga_trpg.runtime.journal import InMemoryJournal
from saga_trpg.runtime.replay import replay_recorded_results


class FailingRules:
    name = "failing-rules"
    version = "0.1.0"

    def apply(self, *, state, event, dice):
        raise AssertionError("rules.apply must not be called for narrative_turn")


class SpyNarrator:
    def __init__(self) -> None:
        self.calls: list[NarrationRequest] = []

    async def render(self, request: NarrationRequest) -> str:
        self.calls.append(request)
        return "spy narration"


def turn_request(
    *actions,
    instance: str = "host-1",
    chat: str = "chat-hook-1",
    version: int = 4,
    client_proposal: dict | None = None,
) -> dict:
    actions = actions or (("u1", "주인장에게 가볍게 인사를 건넨다."),)
    body: dict = {
        "saga": {
            "schema_version": version,
            "host_instance_id": instance,
            "chat_id": chat,
            "purpose": "action",
            "actor_id": "player",
            "actions": [{"message_id": key, "text": text} for key, text in actions],
        }
    }
    if client_proposal is not None:
        body["client_proposal"] = client_proposal
    return body


# --- Runtime narrative_turn invariants --------------------------------------------------------


@pytest.mark.parametrize(
    "rules",
    [
        GenericFantasyRules(),
        SRD521Rules("0.3.0"),
        DataRules(load_rulebook(rulebook_path("srd-5.2.1"))),
        DataRules(load_rulebook(rulebook_path("fate-condensed"))),
        FailingRules(),
    ],
)
def test_runtime_narrative_turn_is_state_neutral_and_replayable(rules):
    dice = CountingDice()
    initial = WorldState(
        world_id="w-narr",
        actors={
            "player": ActorState(actor_id="player", hp=17, max_hp=20, inventory={"torch": 1}),
            "npc": ActorState(actor_id="npc", hp=10, max_hp=10),
        },
        flags={"clue": True},
    )
    engine = RuntimeEngine(initial_state=initial, rules=rules, dice=dice, journal=InMemoryJournal())
    event = GameEvent(event_id="e1", kind=NARRATIVE_TURN_KIND, actor_id="player", payload={})

    first = engine.dispatch(event)
    assert first.replayed is False
    assert first.entry.committed.seq == 1
    assert first.entry.result.rolls == []
    assert first.entry.result.patches == []
    assert first.entry.result.resolution is None
    assert first.entry.result.trace
    assert dice.calls == 0
    assert first.entry.result.state.model_dump(exclude={"seq"}) == initial.model_dump(
        exclude={"seq"}
    )

    # Idempotent retry
    second = engine.dispatch(event)
    assert second.replayed is True
    assert second.entry == first.entry
    assert len(engine.journal.entries()) == 1

    # Deterministic replay from genesis
    rebuilt = replay_recorded_results(initial, engine.journal.entries())
    assert rebuilt == engine.state


def test_runtime_narrative_turn_rejects_unknown_actor_and_extra_payload():
    dice = CountingDice()
    initial = WorldState(
        world_id="w-narr-reject",
        actors={"player": ActorState(actor_id="player", hp=20, max_hp=20)},
    )
    engine = RuntimeEngine(
        initial_state=initial, rules=FailingRules(), dice=dice, journal=InMemoryJournal()
    )
    with pytest.raises(ValueError, match="unknown actor"):
        engine.dispatch(GameEvent(kind=NARRATIVE_TURN_KIND, actor_id="ghost", payload={}))
    with pytest.raises(ValueError):
        engine.dispatch(GameEvent(kind=NARRATIVE_TURN_KIND, actor_id="player", payload={"hp": 999}))
    assert engine.journal.entries() == []
    assert dice.calls == 0


def test_sqlite_repository_replays_mixed_rule_and_narrative_turns_after_restart(tmp_path):
    db_path = tmp_path / "narr.sqlite3"
    repo = SQLiteWorldRepository(db_path, dice=CountingDice())
    srd = repo.registry.get("srd-5.2.1", "0.6.0")
    repo.create_world(srd.example_world("w1"), ruleset_id="srd-5.2.1", ruleset_version="0.6.0")
    repo.dispatch("w1", GameEvent(event_id="e1", kind="narrative_turn", actor_id="player"))
    repo.dispatch(
        "w1",
        GameEvent(
            event_id="e2",
            kind="attack",
            actor_id="player",
            payload={"target_id": "opponent", "advantage": False, "disadvantage": False},
        ),
    )
    repo.dispatch("w1", GameEvent(event_id="e3", kind="narrative_turn", actor_id="player"))
    state_before = repo.get_state("w1")

    restarted = SQLiteWorldRepository(db_path, dice=CountingDice())
    assert restarted.replay("w1") == state_before
    assert len(restarted.entries("w1")) == 3


# --- Protocol v4 & Hook-mode POST /v1/saga/turn invariants ------------------------------------


def test_hook_endpoint_commits_pure_rp_without_llm_keys_and_replays_on_rerender(tmp_path):
    narrator = SpyNarrator()
    repo, dice, intent, client = setup(tmp_path, narrator=narrator)

    first = client.post("/v1/saga/turn", json=turn_request())
    assert first.status_code == 200, first.text
    payload = first.json()["saga"]
    assert payload["schema_version"] == 4
    assert payload["identity_version"] == 3
    assert payload["replayed"] is False
    assert payload["seq"] == 1
    assert payload["rolls"] == []
    assert payload["patches"] == []
    assert payload["display"]
    assert isinstance(payload["prompt_block"], str) and payload["prompt_block"]
    assert dice.calls == 0
    assert intent.calls == 0
    assert narrator.calls == []

    # 10 regenerations reuse the committed no-op event with zero extra journal entries.
    entries = repo.entries(payload["world_id"], branch_id=payload["branch_id"])
    for _ in range(10):
        again = client.post("/v1/saga/turn", json=turn_request())
        assert again.status_code == 200
        assert again.json()["saga"] == {**payload, "replayed": True}
    assert repo.entries(payload["world_id"], branch_id=payload["branch_id"]) == entries
    assert dice.calls == 0
    assert narrator.calls == []


def test_hook_endpoint_executes_commands_and_rejects_malformed_commands(tmp_path):
    narrator = SpyNarrator()
    repo, dice, intent, client = setup(tmp_path, narrator=narrator)

    attack = client.post("/v1/saga/turn", json=turn_request(("u1", "attack opponent")))
    assert attack.status_code == 200, attack.text
    first = attack.json()["saga"]
    assert first["replayed"] is False
    assert first["rolls"]
    assert dice.calls > 0
    assert narrator.calls == []

    # Follow up with a pure RP turn on the same branch.
    rp = client.post(
        "/v1/saga/turn",
        json=turn_request(("u1", "attack opponent"), ("u2", "숨을 고르며 주변을 살핀다.")),
    )
    assert rp.status_code == 200, rp.text
    second = rp.json()["saga"]
    assert second["seq"] == 2
    assert second["branch_id"] == first["branch_id"] == "main"
    assert second["state"]["actors"]["opponent"]["hp"] == first["state"]["actors"]["opponent"]["hp"]

    # Malformed command verb still returns 422 instead of silently becoming narrative_turn.
    bad = client.post(
        "/v1/saga/turn",
        json=turn_request(
            ("u1", "attack opponent"),
            ("u2", "숨을 고르며 주변을 살핀다."),
            ("u3", "move"),
        ),
    )
    assert bad.status_code == 422
    assert "Supported commands" in bad.json()["detail"]


def test_protocol_v4_preserves_v2_and_v3_identities(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    v2 = client.post("/v1/chat/completions", json=request(("u1", "attack opponent"), version=2))
    assert v2.status_code == 200
    saved_v2 = v2.json()["saga"]

    v4_hook = client.post(
        "/v1/saga/turn",
        json=turn_request(("u1", "attack opponent"), chat="chat-1", version=4),
    )
    assert v4_hook.status_code == 200
    saved_v4 = v4_hook.json()["saga"]
    assert saved_v4["world_id"] == saved_v2["world_id"]
    assert saved_v4["event_id"] == saved_v2["event_id"]
    assert saved_v4["replayed"] is True
    assert dice.calls == len(saved_v2["rolls"])


def test_hook_endpoint_validates_client_proposal_and_ignores_it_on_rerender(tmp_path):
    repo, dice, intent, client = setup(tmp_path)

    # Forged / invalid client proposal is rejected before any commit.
    forged = client.post(
        "/v1/saga/turn",
        json=turn_request(
            ("u1", "상대를 벤다"),
            client_proposal={"kind": "hack_hp", "payload": {"hp": 0}},
        ),
    )
    assert forged.status_code == 422
    assert dice.calls == 0

    # Valid rulebook client proposal (e.g. from RisuAI aux model) is validated & committed.
    valid = client.post(
        "/v1/saga/turn",
        json=turn_request(
            ("u1", "상대를 벤다"),
            client_proposal={
                "kind": "attack",
                "payload": {"target_id": "opponent", "advantage": False, "disadvantage": False},
            },
        ),
    )
    assert valid.status_code == 200, valid.text
    committed = valid.json()["saga"]
    assert committed["rolls"]
    rolls_before = dice.calls

    # Rerender with a different proposal reuses the committed event without re-rolling.
    rerender = client.post(
        "/v1/saga/turn",
        json=turn_request(
            ("u1", "상대를 벤다"),
            client_proposal={"kind": "narrative_turn", "payload": {}},
        ),
    )
    assert rerender.status_code == 200
    assert rerender.json()["saga"]["event_id"] == committed["event_id"]
    assert rerender.json()["saga"]["replayed"] is True
    assert rerender.json()["saga"]["rolls"] == committed["rolls"]
    assert dice.calls == rolls_before


def test_hook_prompt_block_hides_unseen_map_rooms_and_actor_locations(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    # Create a world with a secret room connected to the starting room.
    body = turn_request(("u1", "주변을 둘러본다"), chat="chat-secret-map")
    world_id = client.app.state.risu_bridge.world_id_for(TurnRequest.model_validate(body).saga)
    secret_map = MapState(
        start_room="hall",
        rooms={
            "hall": Room(
                name="입구 홀",
                description="돌바닥의 넓은 방",
                exits={"north": Exit(to_room="vault", label="북쪽 철문", kind="door")},
            ),
            "vault": Room(
                name="비밀 보물고",
                description="황금이 가득한 숨겨진 방",
                exits={"south": Exit(to_room="hall", label="남쪽 문")},
            ),
        },
    )
    srd = repo.registry.get("srd-5.2.1", "0.6.0")
    initial = srd.example_world(world_id)
    initial.map = secret_map
    initial.actors["player"].location = "hall"
    initial.actors["player"].visited_rooms = {"hall"}
    initial.actors["opponent"].location = "vault"
    repo.create_world(initial, ruleset_id="srd-5.2.1", ruleset_version="0.6.0")

    res = client.post("/v1/saga/turn", json=body)
    assert res.status_code == 200, res.text
    block = res.json()["saga"]["prompt_block"]
    assert "player: 20/20" in block
    assert "입구 홀" in block
    assert "북쪽 철문" in block
    assert "???" in block
    assert "비밀 보물고" not in block
    assert "황금이 가득한" not in block
    assert "vault" not in block


def test_chat_completions_protocol_v4_allows_pure_rp_with_fallback(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    res = client.post(
        "/v1/chat/completions",
        json=request(("u1", "모닥불 옆에 앉아 쉬어간다."), version=4),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["saga"]["schema_version"] == 4
    assert body["saga"]["rolls"] == []
    assert body["choices"][0]["message"]["content"]
    assert dice.calls == 0


def test_exported_risu_turn_schema_matches_model():
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas/risu-turn.schema.json").read_text(encoding="utf-8")
    )
    assert schema == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **TurnRequest.model_json_schema(),
    }


def test_intent_context_endpoint_is_readonly_and_detects_commands_and_reroll(tmp_path):
    import sqlite3

    repo, dice, intent, client = setup(tmp_path)

    def world_count() -> int:
        with sqlite3.connect(repo.path) as conn:
            return int(conn.execute("SELECT count(*) FROM worlds").fetchone()[0])

    # 1. Explicit command -> is_command=True, does not create a world
    cmd_res = client.post("/v1/saga/intent-context", json=turn_request(("u1", "attack opponent")))
    assert cmd_res.status_code == 200, cmd_res.text
    assert cmd_res.json() == {
        "already_committed": False,
        "is_command": True,
        "system_instruction": None,
        "context": None,
    }
    assert world_count() == 0

    # 2. New natural-language turn -> grounded context without creating a world
    nl_body = turn_request(("u1", "상대를 검으로 베어 공격한다."))
    ctx_res = client.post("/v1/saga/intent-context", json=nl_body)
    assert ctx_res.status_code == 200, ctx_res.text
    ctx_data = ctx_res.json()
    assert ctx_data["already_committed"] is False
    assert ctx_data["is_command"] is False
    assert "attack" in ctx_data["context"]["actions"]
    assert "opponent" in ctx_data["context"]["actors"]
    assert world_count() == 0

    # 3. Commit the turn via /v1/saga/turn -> subsequent intent-context reports already_committed
    commit_res = client.post(
        "/v1/saga/turn",
        json=turn_request(
            ("u1", "상대를 검으로 베어 공격한다."),
            client_proposal={
                "kind": "attack",
                "payload": {"target_id": "opponent", "advantage": False, "disadvantage": False},
            },
        ),
    )
    assert commit_res.status_code == 200, commit_res.text
    assert len(commit_res.json()["saga"]["rolls"]) >= 1

    reroll_ctx = client.post("/v1/saga/intent-context", json=nl_body)
    assert reroll_ctx.status_code == 200, reroll_ctx.text
    assert reroll_ctx.json()["already_committed"] is True


def test_intent_only_env_enables_status_and_ui_intent_llm_override_rejects_bad_url(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from saga_trpg.api.app import create_app

    monkeypatch.setenv("TRPG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SAGA_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.delenv("SAGA_NARRATOR_MODEL", raising=False)
    monkeypatch.setenv("SAGA_INTENT_MODEL", "google/gemini-2.5-flash")
    app = create_app()
    with TestClient(app) as client:
        status = client.get("/v1/saga/status")
        assert status.status_code == 200
        assert status.json() == {
            "status": "ok",
            "intent_configured": True,
            "narrator_configured": False,
        }
        bad = client.post(
            "/v1/saga/turn",
            json={
                **turn_request(("u1", "상대를 공격한다.")),
                "intent_llm": {
                    "base_url": "http://169.254.169.254/latest/meta-data",
                    "model": "x",
                },
            },
        )
        assert bad.status_code == 422
