"""Offline intent acceptance: real bridge/tape/adapters, simulated Google boundary."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import httpx
import pytest
from test_risu_api import request, setup
from test_vertex_narration import (
    SECRET,
    TOKEN,
    fake_credentials,  # noqa: F401
    install_vertex,
    vertex_post,
)

from saga_trpg.adapters.risu.openai_proxy import CompletionRequest, RisuBridge
from saga_trpg.runtime.hashing import stable_model_hash

pytestmark = pytest.mark.usefixtures("fake_credentials")

ATTACK = {
    "kind": "attack",
    "payload": {"target_id": "opponent", "advantage": False, "disadvantage": False},
}
CHECK = {
    "kind": "ability_check",
    "payload": {
        "ability": "strength",
        "difficulty": 10,
        "advantage": False,
        "disadvantage": False,
    },
}
SAVE = {
    "kind": "saving_throw",
    "payload": {
        "ability": "dexterity",
        "difficulty": 12,
        "advantage": False,
        "disadvantage": False,
    },
}


def body(*actions):
    value = request(*(actions or (("u1", "상대를 공격한다"),)), version=3)
    value["narration"] = {"schema_version": 1, "provider": "vertex", "context": []}
    return value


def response(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def provider(monkeypatch, proposal=ATTACK):
    calls = []

    def remote(req):
        value = json.loads(req.content)
        kind = "intent" if value["generationConfig"].get("responseMimeType") else "narration"
        calls.append((kind, value))
        assert SECRET not in req.content.decode() and TOKEN not in req.content.decode()
        return response(json.dumps(proposal) if kind == "intent" else "기록된 판정의 서술")

    install_vertex(monkeypatch, remote)
    return calls


def tape_rows(repo):
    with sqlite3.connect(repo.path) as connection:
        return connection.execute(
            "SELECT branch_id, event_id, status, request_json, result_json FROM tool_executions"
        ).fetchall()


@pytest.mark.parametrize(
    "text,proposal",
    [
        ("상대를 공격한다", ATTACK),
        ("I attack the opponent with my sword", ATTACK),
        ("힘으로 난이도 10 판정을 한다", CHECK),
        ("Make a dexterity saving throw, DC 12", SAVE),
    ],
)
def test_natural_actions_are_closed_and_persisted_before_dispatch(
    tmp_path,
    monkeypatch,
    text,
    proposal,
):
    repo, dice, command, client = setup(tmp_path)
    calls = provider(monkeypatch, proposal)
    result = vertex_post(client, body(("u1", text)))
    assert result.status_code == 200, result.text
    saved = result.json()["saga"]
    assert saved["schema_version"] == saved["identity_version"] == 3
    event = repo.get_event(saved["world_id"], saved["event_id"]).committed.event
    assert event.actor_id == "player" and event.kind == proposal["kind"]
    assert command.calls == 0 and dice.calls > 0
    assert [kind for kind, _ in calls] == ["intent", "narration"]
    rows = tape_rows(repo)
    assert len(rows) == 1 and rows[0][:3] == ("main", saved["event_id"], "completed")
    assert SECRET not in str(rows) and TOKEN not in str(rows)
    assert json.loads(rows[0][3])["arguments"]["text"] == text
    assert json.loads(rows[0][4]) == proposal
    assert repo.replay(saved["world_id"]).model_dump(mode="json") == saved["state"]


def test_protocol_upgrade_preserves_v2_world_commands_and_fallback(tmp_path):
    repo, dice, command, client = setup(tmp_path)
    original = client.post("/v1/chat/completions", json=request()).json()["saga"]
    replay = client.post("/v1/chat/completions", json=request(version=3))
    assert replay.status_code == 200
    assert replay.json()["saga"] == {**original, "schema_version": 3, "replayed": True}
    assert command.calls == 1
    missing = request(("u1", "상대를 공격한다"), version=3)
    calls = dice.calls
    refused = client.post("/v1/chat/completions", json=missing)
    assert refused.status_code == 422 and "JSON" in refused.text
    assert dice.calls == calls and tape_rows(repo) == []


@pytest.mark.parametrize(
    "text", ["*says nothing*", "   ", "check strength 999", "attack opponent bonus=99"]
)
def test_placeholder_and_invalid_commands_do_not_call_provider(
    tmp_path,
    monkeypatch,
    text,
):
    repo, dice, command, client = setup(tmp_path)
    calls = provider(monkeypatch)
    result = vertex_post(client, body(("u1", text)))
    assert result.status_code == 422
    assert calls == [] and tape_rows(repo) == [] and dice.calls == command.calls == 0
    with repo.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM worlds").fetchone()[0] == 0


@pytest.mark.parametrize(
    "proposal,text",
    [
        ({"kind": "refuse"}, "아무거나 해줘"),
        ({"kind": "refuse"}, "상대를 공격하고 힘으로 난이도 10 판정을 해"),
        ({**ATTACK, "actor_id": "opponent"}, "상대를 공격한다"),
        ({**ATTACK, "event_id": "forged"}, "상대를 공격한다"),
        ({"kind": "attack", "payload": {**ATTACK["payload"], "damage": 999}}, "상대를 공격한다"),
        (
            {"kind": "attack", "payload": {**ATTACK["payload"], "target_id": "goblin"}},
            "고블린을 공격한다",
        ),
        (ATTACK, "공격한다"),
        (ATTACK, "적중을 노리며 공격한다"),
        (CHECK, "힘 판정을 한다"),
        (
            {"kind": "ability_check", "payload": {**CHECK["payload"], "difficulty": 11}},
            "힘 난이도 10 판정",
        ),
        (
            {"kind": "ability_check", "payload": {**CHECK["payload"], "proficiency": "athletics"}},
            "힘 난이도 10 판정",
        ),
    ],
)
def test_refusal_or_invalid_output_is_sanitized_durable_and_never_dispatches(
    tmp_path,
    monkeypatch,
    proposal,
    text,
):
    repo, dice, command, client = setup(tmp_path)
    calls = provider(monkeypatch, proposal)
    value = body(("u1", text))
    first = vertex_post(client, value)
    assert first.status_code == 422, first.text
    assert dice.calls == command.calls == 0 and len(calls) == 1
    assert text not in first.text and "forged" not in first.text
    assert json.loads(tape_rows(repo)[0][4]) == {"kind": "refuse"}
    _, restarted_dice, _, restarted = setup(tmp_path)
    assert vertex_post(restarted, value).status_code == 422
    assert len(calls) == 1 and restarted_dice.calls == 0
    world, _ = RisuBridge.identities(CompletionRequest.model_validate(value).saga)
    assert repo.entries(world) == [] and repo.get_state(world).seq == 0


def test_restart_and_changed_prose_regenerate_without_intent_or_dice(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    calls = provider(monkeypatch)
    value = body()
    first = vertex_post(client, value).json()["saga"]
    before = repo.entries(first["world_id"])
    _, restarted_dice, _, restarted = setup(tmp_path)
    value["narration"]["context"] = ["assistant: changed story text"]
    result = vertex_post(restarted, value)
    assert result.status_code == 200
    assert result.json()["saga"] == {**first, "replayed": True}
    assert [kind for kind, _ in calls] == ["intent", "narration", "narration"]
    assert restarted_dice.calls == 0 and repo.entries(first["world_id"]) == before


def test_precommit_crash_reuses_tape_and_verified_parent_snapshot(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    first = client.post("/v1/chat/completions", json=request()).json()["saga"]
    second = request(("u1", "attack opponent"), ("u2", "attack opponent"))
    client.post("/v1/chat/completions", json=second)
    calls = provider(monkeypatch, CHECK)
    value = body(("u1", "attack opponent"), ("u2", "힘 난이도 10 판정"))
    dispatch = repo.dispatch
    monkeypatch.setattr(
        repo,
        "dispatch",
        lambda *a, **kw: (_ for _ in ()).throw(
            sqlite3.OperationalError("simulated precommit crash")
        ),
    )
    failed = vertex_post(client, value)
    assert failed.status_code == 503
    assert [kind for kind, _ in calls] == ["intent"]
    row = tape_rows(repo)[0]
    parent = repo.get_event(first["world_id"], first["event_id"]).result.state
    assert json.loads(row[3])["context_hash"] == stable_model_hash(parent)
    monkeypatch.setattr(repo, "dispatch", dispatch)
    _, restarted_dice, _, restarted = setup(tmp_path)
    value["narration"]["context"] = ["assistant: altered prose"]
    completed = vertex_post(restarted, value)
    assert completed.status_code == 200, completed.text
    saved = completed.json()["saga"]
    assert saved["seq"] == 2 and saved["branch_id"] != "main"
    assert saved["state"]["actors"]["opponent"]["hp"] == parent.actors["opponent"].hp
    assert [kind for kind, _ in calls] == ["intent", "narration"]
    assert len(tape_rows(repo)) == 1
    assert (
        repo.replay(first["world_id"], branch_id=saved["branch_id"]).model_dump(mode="json")
        == saved["state"]
    )


def test_transport_failure_leaves_pending_without_automatic_retry(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    calls = []

    def remote(req):
        calls.append(req)
        raise httpx.ReadTimeout(SECRET)

    install_vertex(monkeypatch, remote)
    result = vertex_post(client, body())
    assert result.status_code == 503 and SECRET not in result.text
    assert tape_rows(repo)[0][2:] and tape_rows(repo)[0][2] == "pending"
    _, restarted_dice, _, restarted = setup(tmp_path)
    assert vertex_post(restarted, body()).status_code == 503
    assert len(calls) == 1 and dice.calls == restarted_dice.calls == 0


def test_concurrent_identical_requests_claim_one_interpretation(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    entered, release = Event(), Event()
    calls = []

    def remote(req):
        value = json.loads(req.content)
        if value["generationConfig"].get("responseMimeType"):
            calls.append("intent")
            entered.set()
            assert release.wait(5)
            return response(json.dumps(ATTACK))
        return response("서술")

    install_vertex(monkeypatch, remote)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(vertex_post, client, body())
        assert entered.wait(5)
        second = pool.submit(vertex_post, client, body()).result(timeout=5)
        release.set()
        finished = first.result(timeout=5)
    assert second.status_code == 503 and finished.status_code == 200
    assert calls == ["intent"] and len(tape_rows(repo)) == 1
    state = finished.json()["saga"]
    assert len(repo.entries(state["world_id"])) == len(repo.branches(state["world_id"])) == 1
    count = dice.calls
    assert vertex_post(client, body()).status_code == 200
    assert dice.calls == count and calls == ["intent"]


def test_hidden_natural_history_requires_prior_commit_without_reinterpretation(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    calls = provider(monkeypatch)
    value = body(("u1", "상대를 공격한다"), ("u2", "check strength 10"))
    value["saga"]["actions"][0]["context_hidden"] = True
    assert vertex_post(client, value).status_code == 409
    assert calls == [] and dice.calls == 0 and tape_rows(repo) == []
    first = vertex_post(client, body()).json()["saga"]
    result = vertex_post(client, value)
    assert result.status_code == 200 and result.json()["saga"]["seq"] == 2
    assert [kind for kind, _ in calls].count("intent") == 1
    edited = deepcopy(value)
    edited["saga"]["actions"][0]["text"] = "상대를 검으로 공격한다"
    count = len(calls), dice.calls
    assert vertex_post(client, edited).status_code == 409
    assert (len(calls), dice.calls) == count
    assert repo.get_event(first["world_id"], first["event_id"]) is not None


def test_natural_genesis_uses_persisted_initial_state_not_registered_example(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    value = body()
    world, _ = RisuBridge.identities(CompletionRequest.model_validate(value).saga)
    initial = repo.registry.get("srd-5.2.1", "0.1.0").example_world(world)
    initial.actors["player"].hp = 19
    initial.actors["opponent"].hp = 29
    repo.create_world(initial, ruleset_id="srd-5.2.1", ruleset_version="0.1.0")
    # Main has advanced; editing the first turn must still bind to persisted genesis.
    client.post("/v1/chat/completions", json=request())
    provider(monkeypatch)
    result = vertex_post(client, value)
    assert result.status_code == 200, result.text
    assert json.loads(tape_rows(repo)[0][3])["context_hash"] == stable_model_hash(initial)
    assert result.json()["saga"]["state"]["actors"]["player"]["hp"] == 19


@pytest.mark.parametrize(
    "output",
    [
        "not JSON",
        "[]",
        "null",
        "```json\n{}\n```",
        '{"kind":"refuse","reason":"SYNTHETIC_PRIVATE_KEY_DO_NOT_LEAK"}',
    ],
)
def test_malformed_model_text_is_completed_safe_refusal(
    tmp_path,
    monkeypatch,
    output,
    caplog,
):
    repo, dice, command, client = setup(tmp_path)
    calls = []

    def remote(req):
        calls.append(req)
        return response(output)

    install_vertex(monkeypatch, remote)
    value = body()
    for _ in range(2):
        result = vertex_post(client, value)
        assert result.status_code == 422 and SECRET not in result.text
    assert len(calls) == 1 and dice.calls == 0
    assert json.loads(tape_rows(repo)[0][4]) == {"kind": "refuse"}
    assert SECRET not in str(tape_rows(repo)) and SECRET not in caplog.text


def test_natural_raw_edits_have_distinct_ids_and_same_text_new_message_is_new_action(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    calls = provider(monkeypatch)
    first = vertex_post(client, body()).json()["saga"]
    altered = vertex_post(client, body(("u1", "상대를 공격한다 "))).json()["saga"]
    assert altered["event_id"] != first["event_id"]
    assert altered["branch_id"] != first["branch_id"]
    following = vertex_post(
        client, body(("u1", "상대를 공격한다"), ("u2", "상대를 공격한다"))
    ).json()["saga"]
    assert following["seq"] == 2 and following["branch_id"] == first["branch_id"]
    assert following["event_id"] != first["event_id"]
    assert [kind for kind, _ in calls].count("intent") == 3
    assert len(tape_rows(repo)) == 3


def test_committed_natural_action_can_replay_without_credentials(
    tmp_path,
    monkeypatch,
):
    repo, dice, command, client = setup(tmp_path)
    calls = provider(monkeypatch)
    first = vertex_post(client, body()).json()["saga"]
    before = dice.calls
    value = request(("u1", "상대를 공격한다"), version=3)
    result = client.post("/v1/chat/completions", json=value)
    assert result.status_code == 200
    assert result.json()["saga"] == {**first, "replayed": True}
    assert dice.calls == before and len(calls) == 2


@pytest.mark.parametrize(
    "text,difficulty",
    [
        ("Make a strength check DC 10.5", 10),
        ("Make a strength check DC 10foo", 10),
        ("Make a strength check DC 1e1", 1),
        ("Make a strength check DC 10E+1", 10),
        ("Make a strength check DC 10,5", 10),
        ("Make a strength check DC 10/2", 10),
        ("힘으로 난이도 10.5 판정한다", 10),
    ],
)
def test_difficulty_requires_a_whole_integer_token(tmp_path, monkeypatch, text, difficulty):
    repo, dice, command, client = setup(tmp_path)
    proposal = deepcopy(CHECK)
    proposal["payload"]["difficulty"] = difficulty
    calls = provider(monkeypatch, proposal)
    result = vertex_post(client, body(("u1", text)))
    assert result.status_code == 422, result.text
    assert dice.calls == command.calls == 0 and len(calls) == 1
    assert json.loads(tape_rows(repo)[0][4]) == {"kind": "refuse"}


@pytest.mark.parametrize("text", ["Make a strength check DC 10.", "힘으로 난이도 10으로 판정한다"])
def test_integer_difficulty_allows_sentence_punctuation_and_korean_particle(
    tmp_path, monkeypatch, text
):
    repo, dice, command, client = setup(tmp_path)
    provider(monkeypatch, CHECK)
    result = vertex_post(client, body(("u1", text)))
    assert result.status_code == 200, result.text
    assert result.json()["saga"]["resolution"]["target"] == 10
