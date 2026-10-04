"""Experimental boundary tests: real SQLite dispatch, no alternate simulation."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.experimental.multiplayer.adapter import PlayerAdapter
from saga_trpg.experimental.multiplayer.models import (
    ActionProposal,
    ActionSpec,
    CampaignSession,
    Participant,
    ProposedAction,
)
from saga_trpg.experimental.multiplayer.session import SessionRules
from saga_trpg.modules.generic_fantasy.rules import GenericFantasyRules


def config(mode="turn_based"):
    return CampaignSession(
        world_id="party",
        mode=mode,
        turn_order=("a", "b"),
        participants=(
            Participant(
                participant_id="human",
                actor_id="a",
                participant_type="human",
                permissions=("read", "propose", "message"),
                visible_actor_ids=("a", "b"),
                own_stat_keys=("clue",),
                own_inventory_keys=("key",),
            ),
            Participant(
                participant_id="agent",
                actor_id="b",
                participant_type="ai_agent",
                permissions=("read", "propose", "message"),
                visible_actor_ids=("a", "b"),
                own_stat_keys=("clue",),
            ),
            Participant(
                participant_id="watcher", participant_type="spectator", visible_actor_ids=("a", "b")
            ),
            Participant(participant_id="gm", participant_type="gm", visible_actor_ids=("a",)),
        ),
        actions=(
            ActionSpec(kind="time_advance", description="Advance time."),
            ActionSpec(
                kind="attack",
                description="Attack a visible target.",
                actor_reference_fields=("target_id",),
            ),
        ),
        public_flag_keys=("gate_open",),
    )


def setup(tmp_path, *, mode="turn_based", external_dice=None):
    session = config(mode)
    rules = SessionRules(session, GenericFantasyRules())
    repo = SQLiteWorldRepository(
        tmp_path / "party.sqlite3", rules=rules, external_dice=external_dice
    )
    repo.create_world(
        WorldState(
            world_id="party",
            actors={
                "a": ActorState(actor_id="a", stats={"clue": "DAWN"}, inventory={"key": 1}),
                "b": ActorState(actor_id="b", stats={"clue": "LEFT"}),
                "hidden-spy": ActorState(actor_id="hidden-spy", stats={"secret": "GM_SECRET"}),
            },
            flags={"gate_open": False, "gm_secret": "GM_SECRET"},
        )
    )
    return session, repo


def proposal(participant="human", actor="a", seq=0, key="one", **kwargs):
    return ActionProposal(
        participant_id=participant,
        actor_id=actor,
        expected_parent_seq=seq,
        turn_revision=seq,
        client_action_id=key,
        action=ProposedAction(kind="time_advance", **kwargs),
    )


def test_turn_identity_stale_and_retry_after_restart(tmp_path):
    session, repo = setup(tmp_path)
    a = PlayerAdapter(repo, session, "human")
    b = PlayerAdapter(repo, session, "agent")
    assert b.propose_action(proposal("agent", "b")).code == "not_active"
    assert a.propose_action(proposal("agent", "b")).code == "forbidden"
    first = a.propose_action(proposal())
    assert first.accepted and not first.replayed
    assert b.propose_action(proposal("agent", "b")).code == "stale"
    assert b.propose_action(proposal("agent", "b", 1)).accepted
    restarted = SQLiteWorldRepository(repo.path, rules=SessionRules(session, GenericFantasyRules()))
    retry = PlayerAdapter(restarted, session, "human").propose_action(proposal())
    assert retry.accepted and retry.replayed and retry.event_id == first.event_id
    assert a.propose_action(proposal(payload={"amount": 2})).code == "conflict"
    assert restarted.get_state("party").seq == 2
    assert restarted.replay("party") == restarted.get_state("party")


@pytest.mark.parametrize("participant", ["watcher", "gm"])
def test_readonly_and_gm_do_not_gain_player_mutation(tmp_path, participant):
    session, repo = setup(tmp_path)
    adapter = PlayerAdapter(repo, session, participant)
    assert adapter.get_available_actions() == ()
    assert adapter.propose_action(proposal(participant)).code == "forbidden"
    assert repo.entries("party") == []


def test_guard_runs_inside_transaction_and_raw_events_cannot_bypass_it(tmp_path):
    session, repo = setup(tmp_path)
    with pytest.raises(ValueError):
        repo.dispatch("party", GameEvent(kind="time_advance", actor_id="a"))

    def submit(index):
        other = SQLiteWorldRepository(repo.path, rules=SessionRules(session, GenericFantasyRules()))
        return PlayerAdapter(other, session, "human").propose_action(proposal(key=f"race-{index}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert sum(result.accepted for result in results) == 1
    assert [result.code for result in results].count("stale") == 3
    assert len(repo.entries("party")) == 1


def test_serial_allows_any_player_but_still_checks_parent(tmp_path):
    session, repo = setup(tmp_path, mode="serial")
    b = PlayerAdapter(repo, session, "agent")
    assert b.propose_action(proposal("agent", "b")).accepted
    assert PlayerAdapter(repo, session, "human").propose_action(proposal()).code == "stale"


def test_projection_and_errors_do_not_reveal_hidden_or_other_private_state(tmp_path):
    session, repo = setup(tmp_path)
    adapter = PlayerAdapter(repo, session, "human")
    view = adapter.get_visible_state()
    encoded = view.model_dump_json()
    for secret in ("hidden-spy", "GM_SECRET", "LEFT"):
        assert secret not in encoded
    assert view.own_stats == {"clue": "DAWN"}
    assert view.own_inventory == {"key": 1}
    assert adapter.inspect_target("hidden-spy") == adapter.inspect_target("does-not-exist")
    replies = []
    for target in ("hidden-spy", "does-not-exist"):
        action = proposal().model_copy(
            update={"action": ProposedAction(kind="attack", payload={"target_id": target})}
        )
        replies.append(adapter.propose_action(action))
    assert replies[0] == replies[1]
    assert replies[0].code == "target_unavailable"
    view.own_inventory["key"] = 99
    assert repo.get_state("party").actors["a"].inventory["key"] == 1


def test_branch_prefix_retries_and_cursor_are_projected(tmp_path):
    session, repo = setup(tmp_path)
    first = PlayerAdapter(repo, session, "human").propose_action(proposal())
    repo.fork("party", branch_id="rewind", at_event_id=first.event_id)
    fork = session.model_copy(update={"branch_id": "rewind"})
    a = PlayerAdapter(repo, fork, "human")
    assert a.propose_action(proposal()).replayed
    b = PlayerAdapter(repo, fork, "agent")
    assert b.propose_action(proposal("agent", "b", 1)).accepted
    assert repo.get_state("party").seq == 1
    page = b.get_recent_visible_events(after_seq=1)
    assert page.next_seq == 2 and len(page.events) == 1
    assert "state_hash" not in page.model_dump_json()
    assert repo.replay("party", branch_id="rewind") == repo.get_state("party", branch_id="rewind")


@pytest.mark.parametrize("shared_identity", [False, True])
def test_separate_processes_use_persistent_admission_and_idempotency(tmp_path, shared_identity):
    session, repo = setup(tmp_path)
    code = """
import sys
from test_multiplayer import config, proposal
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.experimental.multiplayer.session import SessionRules
from saga_trpg.experimental.multiplayer.adapter import PlayerAdapter
from saga_trpg.modules.generic_fantasy.rules import GenericFantasyRules
session = config()
repo = SQLiteWorldRepository(sys.argv[1], rules=SessionRules(session, GenericFantasyRules()))
reply = PlayerAdapter(repo, session, 'human').propose_action(proposal(key=sys.argv[2]))
print(reply.model_dump_json())
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path(__file__).parent), "src"))}
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(repo.path),
                "shared" if shared_identity else str(index),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for index in range(4)
    ]
    results = []
    for process in processes:
        output, error = process.communicate(timeout=15)
        assert process.returncode == 0, error
        results.append(json.loads(output))
    assert sum(r["accepted"] and not r["replayed"] for r in results) == 1
    assert sum(r["replayed"] for r in results) == (3 if shared_identity else 0)
    assert len(repo.entries(session.world_id)) == 1


def test_changed_manifest_cannot_widen_views_or_resume_under_another_policy(tmp_path):
    from saga_trpg.adapters.storage.database import RepositoryVersionError

    session, repo = setup(tmp_path)
    changed = session.model_copy(update={"public_flag_keys": ("gm_secret",)})
    with pytest.raises(RepositoryVersionError):
        PlayerAdapter(repo, changed, "human")
    changed_repo = SQLiteWorldRepository(
        repo.path, rules=SessionRules(changed, GenericFantasyRules())
    )
    with pytest.raises(RepositoryVersionError):
        changed_repo.get_state("party")


def test_external_tools_stay_on_tape_across_retry_fork_and_failed_commit(tmp_path, monkeypatch):
    from test_tool_tape import FakeTool, binding

    tool = FakeTool()
    session, repo = setup(tmp_path, external_dice=binding(tool))
    repo.snapshot_interval = 1
    a = PlayerAdapter(repo, session, "human")
    attack = proposal().model_copy(
        update={
            "action": ProposedAction(
                kind="attack",
                payload={
                    "target_id": "b",
                    "attack_bonus": 20,
                    "target_ac": 1,
                    "damage": "1d6+1",
                },
            )
        }
    )

    def fail_snapshot(*args):
        raise OSError("injected failure")

    monkeypatch.setattr(repo, "_snapshot", fail_snapshot)
    assert a.propose_action(attack).code == "unavailable"
    assert repo.entries("party") == [] and len(tool.calls) == 2
    reopened = SQLiteWorldRepository(
        repo.path, rules=SessionRules(session, GenericFantasyRules()), external_dice=binding(tool)
    )
    first = PlayerAdapter(reopened, session, "human").propose_action(attack)
    assert first.accepted
    assert PlayerAdapter(reopened, session, "human").propose_action(attack).replayed
    reopened.fork("party", branch_id="copy", at_event_id=first.event_id)
    assert (
        PlayerAdapter(reopened, session.model_copy(update={"branch_id": "copy"}), "human")
        .propose_action(attack)
        .replayed
    )
    assert reopened.replay("party") == reopened.get_state("party")
    assert len(tool.calls) == 2


def test_ambiguous_external_execution_never_retries_and_blocks_other_actions(tmp_path):
    from test_tool_tape import FakeTool, binding

    class LostResponse(FakeTool):
        def execute(self, call, *, idempotency_key):
            super().execute(call, idempotency_key=idempotency_key)
            raise TimeoutError("response lost")

    tool = LostResponse()
    session, repo = setup(tmp_path, external_dice=binding(tool))
    attack = proposal().model_copy(
        update={
            "action": ProposedAction(
                kind="attack",
                payload={
                    "target_id": "b",
                    "attack_bonus": 20,
                    "target_ac": 1,
                    "damage": "1d6+1",
                },
            )
        }
    )
    a = PlayerAdapter(repo, session, "human")
    assert a.propose_action(attack).code == "unavailable"
    restarted = SQLiteWorldRepository(
        repo.path, rules=SessionRules(session, GenericFantasyRules()), external_dice=binding(tool)
    )
    a = PlayerAdapter(restarted, session, "human")
    assert a.propose_action(attack).code == "pending"
    assert a.propose_action(proposal(key="new")).code == "pending"
    assert len(tool.calls) == 1 and restarted.entries("party") == []


@pytest.mark.asyncio
async def test_memory_scope_and_metadata_are_filtered_and_failure_is_soft(tmp_path):
    from saga_trpg.ports.memory import MemoryHit

    session, repo = setup(tmp_path)
    first = PlayerAdapter(repo, session, "human").propose_action(proposal())

    class FakeMemory:
        async def retrieve(self, **kwargs):
            assert kwargs == {
                "world_id": "party",
                "actor_scope": "a",
                "query": "what happened",
                "limit": 8,
            }
            base = dict(
                world_id="party",
                branch_id="main",
                actor_scope="a",
                event_id=first.event_id,
                memory_id="m1",
                extra_private_metadata="hidden-spy",
            )
            return [MemoryHit(text="approved", metadata=base)] + [
                MemoryHit(text="PRIVATE", metadata={**base, **change})
                for change in (
                    {"actor_scope": "b"},
                    {"world_id": "other"},
                    {"branch_id": "other"},
                    {"event_id": "future-event"},
                    {"actor_scope": None},
                )
            ]

    adapter = PlayerAdapter(repo, session, "human", memory=FakeMemory())
    hits = await adapter.recall_memory("what happened")
    assert len(hits) == 1 and hits[0].text == "approved"
    assert "hidden-spy" not in hits[0].model_dump_json()
    assert adapter.memory_retrievals[0].query.as_of_event == first.event_id

    class FailedMemory:
        async def retrieve(self, **kwargs):
            raise OSError("provider failed with private details")

    failed = PlayerAdapter(repo, session, "human", memory=FailedMemory())
    assert await failed.recall_memory("query") == ()
    assert failed.memory_retrievals[0].status == "failed"
    assert repo.get_state("party").seq == 1


@pytest.mark.asyncio
async def test_audience_speaker_projection_and_failure_leave_commit_unchanged(tmp_path):
    from saga_trpg.presentation.models import NarrativeSource
    from saga_trpg.presentation.service import present_narrative

    session, repo = setup(tmp_path)
    first = PlayerAdapter(repo, session, "human").propose_action(proposal())
    before = repo.entries("party")

    class FailedConsumer:
        async def consume(self, events):
            raise RuntimeError("consumer unavailable")

    for audience in ("human", "gm"):
        view = PlayerAdapter(repo, session, audience).get_visible_state()
        for speaker in ("b", "hidden-spy"):
            result = await present_narrative(
                "Hello.",
                source=NarrativeSource(
                    world_id="party",
                    branch_id="main",
                    game_event_id=first.event_id,
                    narration_id=f"{audience}-{speaker}",
                ),
                actor_ids=frozenset(view.approved_speaker_ids),
                enabled=True,
                annotations_json=json.dumps(
                    {
                        "schema_version": 1,
                        "spans": [
                            {
                                "start": 0,
                                "end": 6,
                                "speaker": {"kind": "actor", "actor_id": speaker},
                            }
                        ],
                    }
                ),
                consumer=FailedConsumer(),
            )
            expected = "actor" if audience == "human" and speaker == "b" else "unresolved"
            assert result.events[0].speaker.kind == expected
            assert result.delivery_status == "failed" and result.text == "Hello."
    assert repo.entries("party") == before


@pytest.mark.parametrize("payload", [{}, {"target_id": None}, {"target_id": 123}])
def test_missing_or_malformed_targets_cannot_synthesize_hidden_actors(tmp_path, payload):
    session, repo = setup(tmp_path)
    action = proposal().model_copy(
        update={"action": ProposedAction(kind="attack", payload=payload)}
    )
    response = PlayerAdapter(repo, session, "human").propose_action(action)
    assert response.code == "target_unavailable"
    assert repo.entries("party") == []
    assert set(repo.get_state("party").actors) == {"a", "b", "hidden-spy"}


def test_parent_and_turn_revision_are_checked_independently(tmp_path):
    session, repo = setup(tmp_path)
    PlayerAdapter(repo, session, "human").propose_action(proposal())
    b = PlayerAdapter(repo, session, "agent")
    current = proposal("agent", "b", seq=1)
    assert b.propose_action(current.model_copy(update={"turn_revision": 0})).code == "stale"
    assert b.propose_action(current.model_copy(update={"expected_parent_seq": 0})).code == "stale"
    assert repo.get_state("party").seq == 1


def test_party_communication_has_explicit_audience_and_does_not_commit_state(tmp_path):
    from saga_trpg.experimental.multiplayer.adapter import PartyChannel
    from saga_trpg.experimental.multiplayer.models import ClueClaim, MessageDraft
    from saga_trpg.experimental.multiplayer.session import AdmissionError

    session, repo = setup(tmp_path)
    channel = PartyChannel()
    a = PlayerAdapter(repo, session, "human", channel=channel)
    before = repo.get_state("party")
    sent = a.send_party_message(
        MessageDraft(text="The clue is DAWN.", claims=(ClueClaim(key="clue", value="DAWN"),))
    )
    b = PlayerAdapter(repo, session, "agent", channel=channel)
    assert b.get_party_messages() == (sent,)
    assert sent.sender_actor_id == "a" and sent.recipients == ("a", "b")
    assert sent.after_seq == 0 and sent.message_seq == 1
    spectator = PlayerAdapter(repo, session, "watcher", channel=channel)
    assert spectator.get_party_messages() == ()
    with pytest.raises(AdmissionError, match="forbidden"):
        spectator.send_party_message(MessageDraft(text="Inject a message"))
    repo.fork("party", branch_id="other", at_event_id=None)
    fork = PlayerAdapter(
        repo, session.model_copy(update={"branch_id": "other"}), "agent", channel=channel
    )
    assert fork.get_party_messages() == ()
    assert repo.get_state("party") == before and repo.entries("party") == []
