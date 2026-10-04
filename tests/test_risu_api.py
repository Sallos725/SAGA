from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from saga_trpg.adapters.dice.local import DeterministicDice
from saga_trpg.adapters.risu.openai_proxy import CommandInterpreter, RisuBridge
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.api.app import create_app
from saga_trpg.domain.events import GameEvent


def request(*actions, instance="host-1", chat="chat-1"):
    actions = actions or (("u1", "attack opponent"),)
    return {
        "model": "saga-srd-demo",
        "messages": [{"role": "user", "content": actions[-1][1]}],
        "saga": {
            "schema_version": 1,
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
