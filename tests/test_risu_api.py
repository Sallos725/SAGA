import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from saga_trpg.adapters.dice.local import DeterministicDice
from saga_trpg.adapters.risu.openai_proxy import (
    CommandInterpreter,
    CompletionRequest,
    RisuBridge,
)
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
    assert first["branch_id"] == second["branch_id"] == "main"
    assert second["seq"] == 2
    original = repo.entries(first["world_id"])
    edited = request(("u1", "attack opponent"), ("u2", "check strength 10"))
    third = client.post("/v1/chat/completions", json=edited).json()["saga"]
    assert third["seq"] == 2 and third["event_id"] != second["event_id"]
    assert third["branch_id"] == third["event_id"]
    assert repo.entries(first["world_id"]) == original
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


def test_predispatch_crash_recovers_and_later_foreign_entries_never_merge(tmp_path, monkeypatch):
    repo, dice, intent, client = setup(tmp_path)
    dispatch = repo.dispatch

    def fail_before_dispatch(*args, **kwargs):
        raise RuntimeError("process interruption before dispatch")

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
    # The committed prefix still replays; an entry after it is not part of this history.
    replay = client.post("/v1/chat/completions", json=request()).json()["saga"]
    assert replay == {**saved, "replayed": True}
    assert (dice.calls, intent.calls) == calls
    # The next host action must not build on the foreign entry: it forks from its parent.
    body = request(("u1", "attack opponent"), ("u2", "check strength 10"))
    following = client.post("/v1/chat/completions", json=body).json()["saga"]
    assert following["seq"] == 2 and following["branch_id"] == following["event_id"]
    assert [
        entry.committed.event.event_id
        for entry in repo.entries(saved["world_id"], branch_id=following["branch_id"])
    ] == [saved["event_id"], following["event_id"]]


def test_contaminated_prefix_rejects_without_dispatch(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    body = request()
    turn = CompletionRequest.model_validate(body).saga
    world_id, identities = RisuBridge.identities(turn)
    definition = repo.registry.get("srd-5.2.1", "0.1.0")
    repo.create_world(
        definition.example_world(world_id), ruleset_id="srd-5.2.1", ruleset_version="0.1.0"
    )
    check = {"ability": "strength", "difficulty": 10}
    for event_id in ("foreign", identities[0]):
        repo.dispatch(
            world_id,
            GameEvent(kind="ability_check", event_id=event_id, actor_id="player", payload=check),
        )
    calls = dice.calls
    assert client.post("/v1/chat/completions", json=body).status_code == 409
    assert dice.calls == calls and intent.calls == 0


def test_foreign_branch_ending_at_the_parent_id_is_never_continued(tmp_path):
    repo, _, _, client = setup(tmp_path)
    actions = [("a", "attack opponent"), ("b", "attack opponent"), ("c", "attack opponent")]
    for index in range(3):
        saved = client.post("/v1/chat/completions", json=request(*actions[: index + 1])).json()
    world_id = saved["saga"]["world_id"]
    _, identities = RisuBridge.identities(CompletionRequest.model_validate(request(*actions)).saga)
    check = {"ability": "strength", "difficulty": 10}
    # Explicit ids are branch scoped, so another writer may end a different history at B's id.
    for branch_id in ("aa-foreign", "zz-foreign"):
        repo.fork(world_id, branch_id=branch_id, at_event_id=None)
        for event_id in ("foreign", identities[1]):
            repo.dispatch(
                world_id,
                GameEvent(
                    kind="ability_check", event_id=event_id, actor_id="player", payload=check
                ),
                branch_id=branch_id,
            )
    expected = repo.replay(world_id, at_event_id=identities[1])
    foreign = {
        name: repo.entries(world_id, branch_id=name) for name in ("aa-foreign", "zz-foreign")
    }
    edited = client.post(
        "/v1/chat/completions", json=request(*actions[:2], ("d", "check strength 10"))
    )
    assert edited.status_code == 200, edited.text
    result = edited.json()["saga"]
    assert result["seq"] == 3 and result["branch_id"] == result["event_id"]
    assert result["state"]["actors"] == expected.model_dump(mode="json")["actors"]
    assert [
        entry.committed.event.event_id
        for entry in repo.entries(world_id, branch_id=result["branch_id"])
    ] == [*identities[:2], result["event_id"]]
    assert {
        name: repo.entries(world_id, branch_id=name) for name in ("aa-foreign", "zz-foreign")
    } == foreign
    replay = client.post("/v1/chat/completions", json=request(*actions[:2])).json()["saga"]
    assert replay["replayed"] and replay["branch_id"] == "main"


def test_linear_session_appends_to_one_branch(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    actions, results = [], []
    for index in range(12):
        actions.append((f"m{index}", "check strength 10"))
        response = client.post("/v1/chat/completions", json=request(*actions))
        assert response.status_code == 200, response.text
        results.append(response.json()["saga"])
    world_id = results[0]["world_id"]
    assert [result["seq"] for result in results] == list(range(1, 13))
    assert {result["branch_id"] for result in results} == {"main"}
    assert [branch.branch_id for branch in repo.branches(world_id)] == ["main"]
    with repo.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM entries").fetchone()[0] == 12
    calls = dice.calls, intent.calls
    # Regenerating any earlier turn reads its record from the same branch.
    for index in (0, 5, 11):
        again = client.post("/v1/chat/completions", json=request(*actions[: index + 1]))
        assert again.json()["saga"] == {**results[index], "replayed": True}
    assert (dice.calls, intent.calls) == calls
    assert len(repo.branches(world_id)) == 1


def test_only_a_semantic_edit_forks_and_both_lines_continue_in_place(tmp_path):
    repo, _, _, client = setup(tmp_path)

    def play(*actions):
        response = client.post("/v1/chat/completions", json=request(*actions))
        assert response.status_code == 200, response.text
        return response.json()["saga"]

    base = [("m1", "attack opponent"), ("m2", "attack opponent")]
    play(*base[:1])
    play(*base)
    original = play(*base, ("m3", "attack opponent"))
    edited = play(*base, ("m3", "check strength 10"))
    world_id = original["world_id"]
    assert original["branch_id"] == "main" and edited["branch_id"] == edited["event_id"]
    assert len(repo.branches(world_id)) == 2
    # Each line keeps appending to its own branch; no further branches appear.
    edited_next = play(*base, ("m3", "check strength 10"), ("m4", "attack opponent"))
    original_next = play(*base, ("m3", "attack opponent"), ("m4", "attack opponent"))
    assert edited_next["branch_id"] == edited["branch_id"] and edited_next["seq"] == 4
    assert original_next["branch_id"] == "main" and original_next["seq"] == 4
    assert edited_next["event_id"] != original_next["event_id"]
    assert len(repo.branches(world_id)) == 2
    assert repo.replay(world_id).model_dump(mode="json") == original_next["state"]
    assert (
        repo.replay(world_id, branch_id=edited["branch_id"]).model_dump(mode="json")
        == edited_next["state"]
    )


def test_per_turn_branch_layout_from_earlier_bridge_continues_without_new_branches(tmp_path):
    repo, dice, intent, client = setup(tmp_path)
    actions = [("m1", "attack opponent"), ("m2", "check strength 10")]
    turn = CompletionRequest.model_validate(request(*actions)).saga
    world_id, identities = RisuBridge.identities(turn)
    definition = repo.registry.get("srd-5.2.1", "0.1.0")
    repo.create_world(
        definition.example_world(world_id), ruleset_id="srd-5.2.1", ruleset_version="0.1.0"
    )
    parent = None
    for (_, text), event_id in zip(actions, identities, strict=True):
        event = asyncio.run(
            CommandInterpreter().interpret(world_id=world_id, actor_id="player", text=text)
        )
        event.event_id = event.idempotency_key = event_id
        event.parent_event_id = parent
        repo.fork(
            world_id, branch_id=event_id, source_branch_id=parent or "main", at_event_id=parent
        )
        repo.dispatch(world_id, event, branch_id=event_id)
        parent = event_id
    calls = dice.calls
    replay = client.post("/v1/chat/completions", json=request(*actions)).json()["saga"]
    assert replay["replayed"] and replay["branch_id"] == replay["event_id"] == identities[-1]
    assert dice.calls == calls and intent.calls == 0
    following = client.post(
        "/v1/chat/completions", json=request(*actions, ("m3", "attack opponent"))
    ).json()["saga"]
    assert following["seq"] == 3 and following["branch_id"] == identities[-1]
    assert len(repo.branches(world_id)) == 3


def test_concurrent_different_edits_of_one_turn_never_share_a_branch(tmp_path):
    repo, _, _, client = setup(tmp_path)
    first = client.post("/v1/chat/completions", json=request()).json()["saga"]
    texts = ["attack opponent", "check strength 10", "save dexterity 12", "check wisdom 11"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda text: client.post(
                    "/v1/chat/completions", json=request(("u1", "attack opponent"), ("u2", text))
                ),
                texts,
            )
        )
    assert all(response.status_code == 200 for response in responses), [
        response.text for response in responses
    ]
    results = [response.json()["saga"] for response in responses]
    assert len({result["branch_id"] for result in results}) == 4
    for result in results:
        assert result["seq"] == 2
        assert [
            entry.committed.event.event_id
            for entry in repo.entries(first["world_id"], branch_id=result["branch_id"])
        ] == [first["event_id"], result["event_id"]]


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
    assert len(repo.branches(saved["world_id"])) == 1


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
    assert len(repo.branches(saved["world_id"])) == 1

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
