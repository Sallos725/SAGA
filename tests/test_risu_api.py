import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from saga_trpg.adapters.dice.local import DeterministicDice
from saga_trpg.adapters.risu.openai_proxy import CommandInterpreter, RisuBridge
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.api.app import create_app
from saga_trpg.domain.events import GameEvent
from saga_trpg.runtime.hashing import stable_value_hash


def request(*actions, instance="host-1", chat="chat-1", version=2):
    actions = actions or (("u1", "attack opponent"),)
    return {
        "model": "saga-srd-demo",
        "messages": [{"role": "user", "content": actions[-1][1]}],
        "saga": {
            "schema_version": version,
            "host_instance_id": instance,
            "chat_id": chat,
            "purpose": "action",
            "actor_id": "player",
            "actions": [{"message_id": key, "text": text} for key, text in actions],
        },
    }


class CountingDice(DeterministicDice):
    def __init__(self):
        super().__init__("risu-test")
        self.calls = 0

    def roll(self, **kwargs):
        self.calls += 1
        return super().roll(**kwargs)


class CountingInterpreter(CommandInterpreter):
    def __init__(self):
        self.calls = 0

    async def interpret(self, **kwargs):
        self.calls += 1
        return await super().interpret(**kwargs)


def setup(tmp_path, *, narrator=None, interpreter=None):
    dice = CountingDice()
    repo = SQLiteWorldRepository(tmp_path / "risu.sqlite3", dice=dice)
    intent = interpreter or CountingInterpreter()
    bridge = RisuBridge(repo, interpreter=intent, narrator=narrator)
    return repo, dice, intent, TestClient(create_app(repository=repo, risu_bridge=bridge))


def test_ten_regenerations_and_restart_never_reinterpret_or_roll(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    first = client.post("/v1/chat/completions", json=request())
    assert first.status_code == 200, first.text
    saved = first.json()["saga"]
    before_calls = dice.calls
    assert before_calls > 0 and intent.calls == 1
    entries = repo.entries(saved["world_id"], branch_id=saved["branch_id"])
    for _ in range(10):
        body = request()
        body["messages"].insert(0, {"role": "assistant", "content": "different prose"})
        result = client.post("/v1/chat/completions", json=body)
        assert result.status_code == 200
        assert result.json()["saga"] == {**saved, "replayed": True}
    assert dice.calls == before_calls and intent.calls == 1
    assert repo.entries(saved["world_id"], branch_id=saved["branch_id"]) == entries
    _, restarted_dice, restarted_intent, restarted = setup(tmp_path)
    assert restarted.post("/v1/chat/completions", json=request()).json()["saga"] == {
        **saved,
        "replayed": True,
    }
    assert restarted_dice.calls == restarted_intent.calls == 0


def test_new_identical_text_edit_and_delete_use_immutable_prefixes(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    first = client.post("/v1/chat/completions", json=request()).json()["saga"]
    second_request = request(("u1", "attack opponent"), ("u2", "attack opponent"))
    second = client.post("/v1/chat/completions", json=second_request).json()["saga"]
    assert first["event_id"] != second["event_id"]
    assert first["branch_id"] != second["branch_id"]
    assert second["seq"] == 2
    assert repo.get_state(first["world_id"], branch_id=first["branch_id"]).seq == 1
    edited = request(("u1", "attack opponent"), ("u2", "check strength 10"))
    third = client.post("/v1/chat/completions", json=edited).json()["saga"]
    assert third["seq"] == 2 and third["event_id"] != second["event_id"]
    assert third["state"]["actors"]["opponent"]["hp"] == first["state"]["actors"]["opponent"]["hp"]
    calls = dice.calls, intent.calls
    assert client.post("/v1/chat/completions", json=request()).json()["saga"]["replayed"]
    assert (dice.calls, intent.calls) == calls
    # Editing an earlier turn cannot silently execute all later actions again.
    invalid = request(("u1", "check wisdom 10"), ("u2", "attack opponent"))
    assert client.post("/v1/chat/completions", json=invalid).status_code == 409
    assert (dice.calls, intent.calls) == calls


def test_invalid_identity_intent_auxiliary_and_stream_never_dispatch(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    malformed = []
    for field in ("host_instance_id", "chat_id", "actions"):
        body = request()
        del body["saga"][field]
        malformed.append(body)
    for purpose in ("continue", "auxiliary"):
        body = request()
        body["saga"]["purpose"] = purpose
        malformed.append(body)
    malformed += [
        request(("u1", "attack opponent"), ("u1", "attack opponent")),
        request(("u1", "reduce hp by 10")),
        request(("u1", "*says nothing*")),
        request(("u1", "check strength 999")),
        request(("u1", "attack opponent bonus=99")),
        {**request(), "stream": True},
    ]
    conflict = deepcopy(request())
    conflict["messages"][-1]["content"] = "check wisdom 10"
    malformed.append(conflict)
    for body in malformed:
        response = client.post("/v1/chat/completions", json=body)
        assert response.status_code == 422, response.text
    assert dice.calls == 0


def test_commit_precedes_narration_and_failure_is_retryable_without_mutation(tmp_path):
    class BrokenNarrator:
        observed_commit = False

        async def render(self, narration):
            self.observed_commit = (
                repo.get_event(
                    narration.world_id,
                    narration.event.event_id,
                    branch_id=narration.event.event_id,
                )
                is not None
            )
            narration.result.state.actors["opponent"].hp = 999
            raise RuntimeError("narrator offline")

    narrator = BrokenNarrator()
    repo, dice, intent, client = setup(tmp_path, narrator=narrator)
    response = client.post("/v1/chat/completions", json=request())
    assert response.status_code == 200, response.text
    saved = response.json()["saga"]
    assert response.json()["choices"][0]["message"]["content"]
    assert saved["state"]["actors"]["opponent"]["hp"] <= 30
    assert narrator.observed_commit
    calls = dice.calls
    assert client.post("/v1/chat/completions", json=request()).json()["saga"]["replayed"]
    assert dice.calls == calls and intent.calls == 1


def test_malformed_interpreter_proposal_cannot_reach_rules(tmp_path):
    class BadIntent:
        async def interpret(self, **kwargs):
            return GameEvent(kind="attack", actor_id="player", payload={"damage": 1000})

    _, dice, _, client = setup(tmp_path, interpreter=BadIntent())
    assert client.post("/v1/chat/completions", json=request()).status_code == 422
    assert dice.calls == 0


def test_host_and_chat_scope_world_identity(tmp_path):
    _, _, _, client = setup(tmp_path)
    results = [
        client.post("/v1/chat/completions", json=body).json()["saga"]
        for body in (request(), request(instance="host-2"), request(chat="chat-2"))
    ]
    assert len({result["world_id"] for result in results}) == 3


def test_concurrent_identical_requests_commit_one_action(tmp_path):
    repo, dice, _, client = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: client.post("/v1/chat/completions", json=request()), range(2))
        )
    assert all(response.status_code == 200 for response in responses)
    first, second = [response.json()["saga"] for response in responses]
    assert first["event_id"] == second["event_id"]
    assert first["state"] == second["state"]
    assert sorted([first["replayed"], second["replayed"]]) == [False, True]
    assert dice.calls == len(first["rolls"])
    assert len(repo.entries(first["world_id"], branch_id=first["branch_id"])) == 1


def test_predispatch_crash_fork_recovers_and_contaminated_history_rejects(tmp_path, monkeypatch):
    repo, dice, intent, client = setup(tmp_path)
    dispatch = repo.dispatch

    def fail_before_dispatch(*args, **kwargs):
        raise RuntimeError("process interruption after branch creation")

    monkeypatch.setattr(repo, "dispatch", fail_before_dispatch)
    with pytest.raises(RuntimeError, match="process interruption"):
        client.post("/v1/chat/completions", json=request())
    assert dice.calls == 0
    monkeypatch.setattr(repo, "dispatch", dispatch)
    saved = client.post("/v1/chat/completions", json=request()).json()["saga"]
    assert saved["seq"] == 1
    repo.dispatch(
        saved["world_id"],
        GameEvent(
            kind="ability_check",
            event_id="foreign",
            actor_id="player",
            payload={"ability": "strength", "difficulty": 10},
        ),
        branch_id=saved["branch_id"],
    )
    calls = dice.calls, intent.calls
    assert client.post("/v1/chat/completions", json=request()).status_code == 409
    assert (dice.calls, intent.calls) == calls


def test_missing_committed_prefix_and_illegal_target_never_roll(tmp_path):
    _, dice, intent, client = setup(tmp_path)
    body = request(("missing", "attack opponent"), ("u2", "check strength 10"))
    assert client.post("/v1/chat/completions", json=body).status_code == 409
    assert intent.calls == 0
    assert (
        client.post("/v1/chat/completions", json=request(("u1", "attack imaginary"))).status_code
        == 422
    )
    assert dice.calls == 0


@pytest.mark.parametrize(
    "command", ["check strength 10 advantage", "save dexterity 12 disadvantage"]
)
def test_supported_check_and_save_commands_use_ruleset_validation(tmp_path, command):
    _, dice, _, client = setup(tmp_path)
    response = client.post("/v1/chat/completions", json=request(("u1", command)))
    assert response.status_code == 200, response.text
    result = response.json()["saga"]
    assert result["resolution"]["outcome"] in {"success", "failure"}
    assert result["state"]["actors"]["opponent"]["hp"] == 30
    assert dice.calls == 2


def test_exported_request_schema_matches_current_contract():
    import json
    from pathlib import Path

    from saga_trpg.adapters.risu.openai_proxy import CompletionRequest

    schema = json.loads(
        (Path(__file__).parents[1] / "schemas/risu-completion.schema.json").read_text()
    )
    schema.pop("$schema")
    assert schema == CompletionRequest.model_json_schema()


@pytest.mark.parametrize(
    "variants",
    [
        ["attack opponent", "Attack opponent", "attack  opponent", "\n attack opponent\t"],
        ["check strength 10", "Check STRENGTH 010", "check  strength 010"],
        ["save dexterity 12 advantage", " SAVE Dexterity 012 ADVANTAGE "],
    ],
)
def test_cosmetic_commands_keep_one_record_without_reinterpreting(tmp_path, variants):
    repo, dice, intent, client = setup(tmp_path)
    responses = [
        client.post("/v1/chat/completions", json=request(("u1", text))) for text in variants
    ]
    assert all(response.status_code == 200 for response in responses)
    saved = responses[0].json()["saga"]
    assert all(response.json()["saga"]["event_id"] == saved["event_id"] for response in responses)
    assert all(response.json()["saga"]["state"] == saved["state"] for response in responses)
    assert dice.calls == len(saved["rolls"]) and intent.calls == 1
    assert len(repo.branches(saved["world_id"])) == 2


def test_context_hidden_committed_prefix_keeps_state_and_drafts_reject(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    actions = []
    for index in range(1, 7):
        actions.append((f"m{index}", "attack opponent"))
        response = client.post("/v1/chat/completions", json=request(*actions))
        assert response.status_code == 200
    previous = response.json()["saga"]
    body = request(*actions, ("m7", "check strength 10"))
    for action in body["saga"]["actions"][:-1]:
        action["context_hidden"] = True
    continued = client.post("/v1/chat/completions", json=body)
    assert continued.status_code == 200, continued.text
    saved = continued.json()["saga"]
    assert saved["seq"] == 7
    assert (
        saved["state"]["actors"]["opponent"]["hp"] == previous["state"]["actors"]["opponent"]["hp"]
    )
    before = dice.calls, intent.calls, repo.branches(saved["world_id"])
    replay = client.post(
        "/v1/chat/completions", json=request(*actions, ("m7", "CHECK strength 010"))
    )
    assert replay.status_code == 200 and replay.json()["saga"]["event_id"] == saved["event_id"]
    unknown = request(("draft", "attack opponent"), ("new", "check strength 10"))
    unknown["saga"]["actions"][0]["context_hidden"] = True
    assert client.post("/v1/chat/completions", json=unknown).status_code == 409
    hidden_latest = deepcopy(body)
    hidden_latest["saga"]["actions"][-1]["context_hidden"] = True
    assert client.post("/v1/chat/completions", json=hidden_latest).status_code == 422
    assert (dice.calls, intent.calls, repo.branches(saved["world_id"])) == before


def seed_legacy(repo, text):
    """Write a frozen v1-style record through existing repository APIs, without this bridge."""
    world_id = "risu-" + stable_value_hash(["saga-risu-v1", "host-1", "chat-1"])
    event_id = "risu-" + stable_value_hash(["saga-risu-action-v1", world_id, None, "u1", text])
    definition = repo.registry.get("srd-5.2.1", "0.1.0")
    repo.create_world(
        definition.example_world(world_id), ruleset_id="srd-5.2.1", ruleset_version="0.1.0"
    )
    repo.fork(world_id, branch_id=event_id, at_event_id=None)
    event = asyncio.run(
        CommandInterpreter().interpret(world_id=world_id, actor_id="player", text=text)
    )
    event.event_id = event.idempotency_key = event_id
    return repo.dispatch(world_id, event, branch_id=event_id).entry


@pytest.mark.parametrize("text", ["attack opponent", "Attack  opponent", "CHECK Strength 010"])
def test_legacy_exact_records_remain_read_only_across_upgrade_and_restart(tmp_path, text):
    repo, _, _, _ = setup(tmp_path)
    original = seed_legacy(repo, text)
    world_id = original.result.state.world_id
    before = repo.branches(world_id)
    for _ in range(2):
        reopened, dice, intent, client = setup(tmp_path)
        for version in (1, 2):
            response = client.post(
                "/v1/chat/completions", json=request(("u1", text), version=version)
            )
            assert response.status_code == 200, response.text
            saved = response.json()["saga"]
            assert saved["event_id"] == original.committed.event.event_id
            assert saved["state"] == original.result.state.model_dump(mode="json")
            assert saved["rolls"] == [roll.model_dump() for roll in original.result.rolls]
            assert saved["legacy_read_only"] and saved["identity_version"] == 1
            for altered in (
                request(("u1", text + " "), version=version),
                request(("u1", text), ("u2", "attack opponent"), version=version),
                request(("u2", "attack opponent"), version=version),
            ):
                assert client.post("/v1/chat/completions", json=altered).status_code == 409
        assert reopened.branches(world_id) == before
        assert dice.calls == intent.calls == 0


def test_v1_cannot_create_worlds_or_bypass_v2_identity(tmp_path):
    _, dice, intent, client = setup(tmp_path)
    assert client.post("/v1/chat/completions", json=request(version=1)).status_code == 409
    first = client.post("/v1/chat/completions", json=request(version=2))
    assert first.status_code == 200
    before = dice.calls, intent.calls
    assert (
        client.post(
            "/v1/chat/completions", json=request(("u1", "Attack opponent"), version=1)
        ).status_code
        == 409
    )
    assert (dice.calls, intent.calls) == before


def test_concurrent_cosmetic_first_submissions_converge_and_reopen(tmp_path):
    repo, dice, _, client = setup(tmp_path)
    variants = ["attack opponent", "Attack  opponent", " ATTACK opponent "]
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(
            pool.map(
                lambda text: client.post("/v1/chat/completions", json=request(("u1", text))),
                variants,
            )
        )
    assert all(response.status_code == 200 for response in responses)
    saved = responses[0].json()["saga"]
    assert {response.json()["saga"]["event_id"] for response in responses} == {saved["event_id"]}
    assert sum(not response.json()["saga"]["replayed"] for response in responses) == 1
    assert dice.calls == len(saved["rolls"])
    assert len(repo.branches(saved["world_id"])) == 2

    class NeverInterpret:
        async def interpret(self, **kwargs):
            raise AssertionError("regeneration must not invoke the intent port")

    _, reopened_dice, _, reopened = setup(tmp_path, interpreter=NeverInterpret())
    response = reopened.post("/v1/chat/completions", json=request(("u1", "aTtAcK opponent")))
    assert response.status_code == 200 and response.json()["saga"]["event_id"] == saved["event_id"]
    assert reopened_dice.calls == 0


def test_canonical_identity_refuses_different_valid_interpreter_proposal(tmp_path):
    class ChangedIntent:
        async def interpret(self, **kwargs):
            return GameEvent(
                kind="ability_check",
                actor_id="player",
                payload={"ability": "strength", "difficulty": 10},
            )

    repo, dice, _, client = setup(tmp_path, interpreter=ChangedIntent())
    response = client.post("/v1/chat/completions", json=request())
    assert response.status_code == 422
    assert "canonical" in response.json()["detail"]
    assert dice.calls == 0
    with repo.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM worlds").fetchone()[0] == 0


@pytest.mark.parametrize("hidden", [False, True])
def test_malformed_earlier_command_rejects_before_any_world_write(tmp_path, hidden):
    repo, dice, intent, client = setup(tmp_path)
    body = request(("u1", "unexecuted draft"), ("u2", "attack opponent"))
    body["saga"]["actions"][0]["context_hidden"] = hidden
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == (409 if hidden else 422)
    assert dice.calls == intent.calls == 0
    with repo.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM worlds").fetchone()[0] == 0


def test_hidden_latest_and_true_first_turn_edit_have_distinct_behavior(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    body = request()
    body["saga"]["actions"][0]["context_hidden"] = True
    assert client.post("/v1/chat/completions", json=body).status_code == 422
    assert dice.calls == intent.calls == 0
    with repo.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM worlds").fetchone()[0] == 0
    attack = client.post("/v1/chat/completions", json=request()).json()["saga"]
    edited = client.post("/v1/chat/completions", json=request(("u1", "check strength 10"))).json()[
        "saga"
    ]
    assert edited["event_id"] != attack["event_id"] and edited["seq"] == attack["seq"] == 1
    assert edited["state"]["actors"]["opponent"]["hp"] == 30
    assert (
        repo.get_state(attack["world_id"], branch_id=attack["branch_id"]).model_dump(mode="json")
        == attack["state"]
    )
