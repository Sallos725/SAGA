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
from saga_trpg.adapters.storage.sqlite import RepositoryNotFound, SQLiteWorldRepository
from saga_trpg.api.app import create_app
from saga_trpg.domain.events import GameEvent
from saga_trpg.modules.catalog import SRD_LATEST
from saga_trpg.rulebooks.loader import rulebook_path
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
    app = create_app(repository=repo, risu_bridge=bridge)
    app.state.risu_bridge = bridge
    return repo, dice, intent, TestClient(app)


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


@pytest.mark.parametrize("turns", [1, 2])
def test_stale_read_of_a_just_committed_action_leaves_no_empty_fork(tmp_path, monkeypatch, turns):
    repo, dice, intent, client = setup(tmp_path)
    bridge = client.app.state.risu_bridge
    actions = [("u1", "attack opponent"), ("u2", "check strength 10")][:turns]
    for index in range(turns):
        saved = client.post("/v1/chat/completions", json=request(*actions[: index + 1])).json()
    saved = saved["saga"]
    before = dice.calls, repo.branches(saved["world_id"])
    read_prefix, stale = bridge._read_prefix, [2]

    def raced(world_id, identities):
        # A concurrent identical request committed after this request's reads.
        if len(identities) == turns and stale[0]:
            stale[0] -= 1
            return None
        return read_prefix(world_id, identities)

    monkeypatch.setattr(bridge, "_read_prefix", raced)
    response = client.post("/v1/chat/completions", json=request(*actions))
    assert response.status_code == 200, response.text
    assert response.json()["saga"] == {**saved, "replayed": True}
    assert (dice.calls, repo.branches(saved["world_id"])) == before


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
        for version in (1, 2, 3):
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


def test_new_bridge_worlds_pin_the_latest_srd_and_existing_010_worlds_keep_working(tmp_path):
    repo, _, _, client = setup(tmp_path)
    saved = client.post("/v1/chat/completions", json=request()).json()["saga"]
    selection = repo.ruleset_for_world(saved["world_id"])
    assert (selection.ruleset_id, selection.ruleset_version) == ("srd-5.2.1", SRD_LATEST)

    body = request(instance="old-host")
    world_id, _ = RisuBridge.identities(CompletionRequest.model_validate(body).saga)
    definition = repo.registry.get("srd-5.2.1", "0.1.0")
    repo.create_world(
        definition.example_world(world_id), ruleset_id="srd-5.2.1", ruleset_version="0.1.0"
    )
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    old = request(("u1", "attack opponent"), ("u2", "damage opponent 5"), instance="old-host")
    assert client.post("/v1/chat/completions", json=old).status_code == 422
    assert repo.ruleset_for_world(world_id).ruleset_version == "0.1.0"


@pytest.mark.parametrize(
    "command,kind,payload",
    [
        ("damage opponent 5", "damage", {"target_id": "opponent", "amount": 5}),
        ("heal player 3", "heal", {"target_id": "player", "amount": 3}),
        (
            "inflict opponent prone",
            "apply_condition",
            {"target_id": "opponent", "condition": "prone"},
        ),
        ("wait 2 rounds", "advance_time", {"amount": 2, "unit": "round"}),
        ("wait 1 hour", "advance_time", {"amount": 1, "unit": "hour"}),
    ],
)
def test_state_commands_commit_without_dice_and_replay(tmp_path, command, kind, payload):
    repo, dice, intent, client = setup(tmp_path)
    response = client.post("/v1/chat/completions", json=request(("u1", command)))
    assert response.status_code == 200, response.text
    result = response.json()["saga"]
    assert result["rolls"] == [] and result["resolution"] is None
    assert dice.calls == 0 and intent.calls == 1
    entry = repo.entries(result["world_id"], branch_id=result["branch_id"])[-1]
    assert (entry.committed.event.kind, entry.committed.event.payload) == (kind, payload)
    again = client.post("/v1/chat/completions", json=request(("u1", command))).json()["saga"]
    assert again == {**result, "replayed": True} and intent.calls == 1


def test_cure_requires_the_condition_and_rejects_without_commit(tmp_path):
    repo, _, _, client = setup(tmp_path)
    assert (
        client.post("/v1/chat/completions", json=request(("u1", "cure opponent prone"))).status_code
        == 422
    )
    turns = [("u1", "inflict opponent prone"), ("u2", "cure opponent prone")]
    for index in range(2):
        response = client.post("/v1/chat/completions", json=request(*turns[: index + 1]))
        assert response.status_code == 200, response.text
    assert response.json()["saga"]["state"]["actors"]["opponent"]["conditions"] == []


@pytest.mark.parametrize(
    "command",
    [
        "damage opponent 0",
        "damage opponent five",
        "inflict opponent exhaustion",
        "wait 1 week",
        "heal player",
    ],
)
def test_malformed_state_commands_never_create_worlds(tmp_path, command):
    repo, dice, _, client = setup(tmp_path)
    assert client.post("/v1/chat/completions", json=request(("u1", command))).status_code == 422
    assert dice.calls == 0
    world_id, _ = RisuBridge.identities(CompletionRequest.model_validate(request()).saga)
    with pytest.raises(RepositoryNotFound):
        repo.ruleset_for_world(world_id)


def test_rejected_divergent_edit_leaves_no_empty_fork_and_a_valid_edit_still_forks(tmp_path):
    repo, dice, _, client = setup(tmp_path)
    turns = [("u1", "attack opponent"), ("u2", "check strength 10")]
    for index in range(2):
        saved = client.post("/v1/chat/completions", json=request(*turns[: index + 1])).json()
    world_id = saved["saga"]["world_id"]
    before = repo.branches(world_id), dice.calls
    # Editing u2 diverges, but the rule rejects it before any commit.
    rejected = client.post(
        "/v1/chat/completions", json=request(turns[0], ("u2", "cure opponent prone"))
    )
    assert rejected.status_code == 422, rejected.text
    assert (repo.branches(world_id), dice.calls) == before
    accepted = client.post(
        "/v1/chat/completions", json=request(turns[0], ("u2", "inflict opponent prone"))
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["saga"]["branch_id"] != "main"
    assert len(repo.branches(world_id)) == 2


def test_concurrent_identical_rejected_edits_both_report_the_rule_error(tmp_path):
    repo, _, _, client = setup(tmp_path)
    turns = [("u1", "attack opponent"), ("u2", "check strength 10")]
    for index in range(2):
        saved = client.post("/v1/chat/completions", json=request(*turns[: index + 1])).json()
    world_id = saved["saga"]["world_id"]
    body = request(turns[0], ("u2", "cure opponent prone"))
    _, identities = RisuBridge.identities(CompletionRequest.model_validate(body).saga)
    # Another request for the same rejected edit created and reserved this fork already.
    repo.fork(world_id, branch_id=identities[-1], at_event_id=identities[0])
    bridge = client.app.state.risu_bridge
    branch_for, raced = bridge._branch_for, [True]

    def shared_fork(world_id, identities):
        branch_id, forked = branch_for(world_id, identities)
        if not forked and raced[0]:
            # ...and discards it after its own rule rejection, before this dispatch.
            raced[0] = False
            repo.discard_empty_branch(world_id, branch_id)
        return branch_id, forked

    bridge._branch_for = shared_fork
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 422, response.text
    assert "does not have condition" in response.json()["detail"]
    assert [item.branch_id for item in repo.branches(world_id)] == ["main"]


def test_inventory_commands_pin_030_and_round_trip(tmp_path):
    repo, dice, _, client = setup(tmp_path)
    turns = [
        ("u1", "give player silver_key 1"),
        ("u2", "give player torch 3"),
        ("u3", "take player torch 2"),
    ]
    for index in range(3):
        response = client.post("/v1/chat/completions", json=request(*turns[: index + 1]))
        assert response.status_code == 200, response.text
    result = response.json()["saga"]
    assert repo.ruleset_for_world(result["world_id"]).ruleset_version == SRD_LATEST
    assert result["state"]["actors"]["player"]["inventory"] == {"silver_key": 1, "torch": 1}
    assert result["rolls"] == [] and dice.calls == 0
    rejected = client.post(
        "/v1/chat/completions", json=request(*turns, ("u4", "take player torch 5"))
    )
    assert rejected.status_code == 422 and "holds 1" in rejected.json()["detail"]
    # Commands are case-normalized like every other command; the key shape is still closed.
    assert (
        client.post(
            "/v1/chat/completions", json=request(*turns, ("u4", "give player silver-key 1"))
        ).status_code
        == 422
    )


def test_bridge_worlds_on_040_apply_condition_effects_from_the_rulebook(tmp_path):
    repo, dice, _, client = setup(tmp_path)
    turns = [("u1", "inflict player prone"), ("u2", "attack opponent")]
    for index in range(2):
        response = client.post("/v1/chat/completions", json=request(*turns[: index + 1]))
        assert response.status_code == 200, response.text
    result = response.json()["saga"]
    assert repo.ruleset_for_world(result["world_id"]).ruleset_version == "0.5.0"
    d20s = [roll for roll in result["rolls"] if roll["roll_key"].startswith("attack:")]
    assert len(d20s) == 2
    assert any("prone" in line for line in result["trace"])


FATE_TEXT = rulebook_path("fate-condensed").read_text(encoding="utf-8")


def fate_request(*actions, instance="fate-host", chat="chat-1", rulebook=FATE_TEXT):
    body = request(*actions, instance=instance, chat=chat)
    if rulebook is not None:
        body["saga"]["rulebook"] = {"content": rulebook}
    return body


def test_attached_rulebook_pins_a_new_world_and_its_commands_come_from_it(tmp_path):
    repo, dice, _, client = setup(tmp_path)
    response = client.post(
        "/v1/chat/completions", json=fate_request(("u1", "overcome investigate 2"))
    )
    assert response.status_code == 200, response.text
    saved = response.json()["saga"]
    assert repo.ruleset_for_world(saved["world_id"]).ruleset_id == "fate-condensed"
    assert saved["resolution"]["outcome"] in {"failure", "tie", "success", "success_with_style"}
    assert dice.calls == 1
    again = client.post("/v1/chat/completions", json=fate_request(("u1", "overcome investigate 2")))
    assert again.json()["saga"] == {**saved, "replayed": True} and dice.calls == 1
    second = client.post(
        "/v1/chat/completions",
        json=fate_request(
            ("u1", "overcome investigate 2"), ("u2", 'create_advantage notice 1 "Mapped route"')
        ),
    )
    assert second.status_code == 200, second.text
    assert second.json()["saga"]["state"]["flags"]["fate_aspects"] or second.json()["saga"][
        "resolution"
    ]["outcome"] in {"failure", "tie"}
    rejected = client.post(
        "/v1/chat/completions",
        json=fate_request(("u1", "overcome investigate 2"), ("u2", "attack opponent")),
    )
    assert rejected.status_code == 422 and "unknown action" in rejected.json()["detail"]
    # The world stays pinned even when a later turn arrives without the attachment.
    bare = client.post(
        "/v1/chat/completions",
        json=fate_request(("u1", "overcome investigate 2"), rulebook=None),
    )
    assert bare.status_code == 200 and bare.json()["saga"]["replayed"]


def test_existing_world_ignores_a_different_attached_rulebook(tmp_path):
    repo, _, _, client = setup(tmp_path)
    first = client.post("/v1/chat/completions", json=request()).json()["saga"]
    assert repo.ruleset_for_world(first["world_id"]).ruleset_id == "srd-5.2.1"
    body = fate_request(("u1", "attack opponent"), ("u2", "damage opponent 3"), instance="host-1")
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200, response.text
    assert repo.ruleset_for_world(first["world_id"]).ruleset_id == "srd-5.2.1"


def test_invalid_attached_rulebook_is_rejected_before_any_world_exists(tmp_path):
    repo, dice, _, client = setup(tmp_path)
    broken = FATE_TEXT.replace('"roll": "4d3-8"', '"roll": "4dX"')
    response = client.post(
        "/v1/chat/completions", json=fate_request(("u1", "overcome investigate 2"), rulebook=broken)
    )
    assert response.status_code == 422, response.text
    assert "checks.fate.roll" in response.json()["detail"]
    assert dice.calls == 0
    world_id = RisuBridge.world_id_for(
        CompletionRequest.model_validate(fate_request(("u1", "overcome investigate 2"))).saga
    )
    with pytest.raises(RepositoryNotFound):
        repo.ruleset_for_world(world_id)


def test_error_message_lists_the_rulebook_commands(tmp_path):
    _, _, _, client = setup(tmp_path)
    response = client.post("/v1/chat/completions", json=fate_request(("u1", "dance")))
    assert response.status_code == 422
    assert "overcome <skill> <difficulty>" in response.json()["detail"]


def test_losing_the_first_pin_race_is_a_conflict_not_a_silent_switch(tmp_path, monkeypatch):
    repo, dice, _, client = setup(tmp_path)
    body = fate_request(("u1", "overcome investigate 2"), instance="race-host")
    world_id = RisuBridge.world_id_for(CompletionRequest.model_validate(body).saga)
    create = repo.create_world

    def racer(initial, **kwargs):
        # Another first request for this chat pinned the SRD rulebook just before us.
        srd = repo.registry.get("srd-5.2.1", SRD_LATEST)
        create(srd.example_world(world_id), ruleset_id="srd-5.2.1", ruleset_version=SRD_LATEST)
        return create(initial, **kwargs)

    monkeypatch.setattr(repo, "create_world", racer)
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 409, response.text
    assert dice.calls == 0
    assert repo.ruleset_for_world(world_id).ruleset_id == "srd-5.2.1"


def test_response_carries_rendered_display_lines_from_the_rulebook(tmp_path):
    _, _, _, client = setup(tmp_path)
    srd = client.post("/v1/chat/completions", json=request(("u1", "inflict opponent prone")))
    display = srd.json()["saga"]["display"]
    assert display["actors"][1] == {"id": "opponent", "items": ["30/30", "prone"]}
    assert display["world"] == ["⏱ 0s"] and display["records"] == []
    fate = client.post(
        "/v1/chat/completions",
        json=fate_request(("u1", 'create_advantage notice 1 "Mapped route"')),
    )
    display = fate.json()["saga"]["display"]
    assert display["actors"] == [
        {"id": "player", "items": ["athletics +1, investigate +2, notice +3"]}
    ]
    assert display["world"] == []
    if fate.json()["saga"]["resolution"]["outcome"] in {"success", "success_with_style"}:
        assert display["records"][0]["label"] == "fate_aspects"
        assert display["records"][0]["items"][0].startswith("Mapped route")


class RecordingMemory:
    def __init__(self, *, fail=False):
        self.writes, self.queries, self.fail = [], [], fail

    async def remember(self, item):
        if self.fail:
            raise RuntimeError("memory down")
        self.writes.append(item)

    async def retrieve(self, *, world_id, query, actor_scope=None, limit=8, **kwargs):
        self.queries.append((world_id, query))
        if self.fail:
            raise RuntimeError("memory down")
        from saga_trpg.ports.memory import MemoryHit

        return [MemoryHit(text="The innkeeper is still owed 30 silver.")]


class CapturingNarrator:
    def __init__(self):
        self.requests = []

    async def render(self, request):
        self.requests.append(request)
        return "prose"


def test_committed_facts_are_remembered_with_history_and_recalled_before_narration(tmp_path):
    memory, narrator = RecordingMemory(), CapturingNarrator()
    dice = CountingDice()
    repo = SQLiteWorldRepository(tmp_path / "risu.sqlite3", dice=dice)
    bridge = RisuBridge(repo, interpreter=CountingInterpreter(), narrator=narrator, memory=memory)
    app = create_app(repository=repo, risu_bridge=bridge)
    client = TestClient(app)
    turns = [("u1", "attack opponent"), ("u2", "damage opponent 3")]
    for index in range(2):
        assert (
            client.post("/v1/chat/completions", json=request(*turns[: index + 1])).status_code
            == 200
        )
    assert [w.event_id for w in memory.writes] == [
        RisuBridge.identities(CompletionRequest.model_validate(request(*turns[: i + 1])).saga)[1][
            -1
        ]
        for i in range(2)
    ]
    assert memory.writes[1].world_id == memory.writes[0].world_id
    assert len(memory.writes[1].metadata["history"]) == 2
    assert memory.writes[1].metadata["history"][0]["event_id"] == memory.writes[0].event_id
    assert memory.writes[1].text and "damage" in memory.writes[1].text
    assert memory.writes[1].metadata["branch_id"] == "main"
    assert memory.queries[-1][1] == "damage opponent 3"
    assert [hit.text for hit in narrator.requests[-1].memories] == [
        "The innkeeper is still owed 30 silver."
    ]
    # Regeneration recalls again for fresh prose but writes nothing new.
    client.post("/v1/chat/completions", json=request(*turns))
    assert len(memory.writes) == 2 and len(memory.queries) == 3


def test_memory_failures_never_block_a_turn(tmp_path):
    memory = RecordingMemory(fail=True)
    dice = CountingDice()
    repo = SQLiteWorldRepository(tmp_path / "risu.sqlite3", dice=dice)
    bridge = RisuBridge(repo, interpreter=CountingInterpreter(), memory=memory)
    client = TestClient(create_app(repository=repo, risu_bridge=bridge))
    response = client.post("/v1/chat/completions", json=request())
    assert response.status_code == 200, response.text
    assert response.json()["saga"]["state"]["actors"]["opponent"]["hp"] <= 30
    assert memory.queries
