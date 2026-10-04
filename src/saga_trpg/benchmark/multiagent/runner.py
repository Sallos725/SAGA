"""Local research runner. All mechanical execution uses SQLiteWorldRepository."""

import hashlib
from pathlib import Path

from saga_trpg.adapters.storage.sqlite import RUNTIME_VERSION, SQLiteWorldRepository
from saga_trpg.benchmark.multiagent.models import (
    AgentDecision,
    AgentDriver,
    DecisionRecord,
    Observation,
    RunArtifact,
    Scenario,
    ToolRecord,
)
from saga_trpg.benchmark.multiagent.rules import FixedEffortDice, RuinRules
from saga_trpg.benchmark.multiagent.validators import validate_run
from saga_trpg.experimental.multiplayer.adapter import PartyChannel, PlayerAdapter
from saga_trpg.experimental.multiplayer.models import ActionProposal
from saga_trpg.experimental.multiplayer.session import SessionRules, turn_at
from saga_trpg.runtime.hashing import stable_model_hash


def load_scenario(path: str | Path) -> Scenario:
    scenario = Scenario.model_validate_json(Path(path).read_text(encoding="utf-8"))
    if scenario.session.world_id != scenario.initial_state.world_id:
        raise ValueError("session and scenario world must match")
    if scenario.session.branch_id != "main" or scenario.initial_state.seq != 0:
        raise ValueError("benchmark runs must start at a fresh genesis")
    return scenario


def source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.py"))
    }


def run_scenario(
    scenario: Scenario,
    drivers: dict[str, AgentDriver],
    database_path: str | Path,
) -> RunArtifact:
    scenario = Scenario.model_validate_json(scenario.model_dump_json())
    players = {p.participant_id: p for p in scenario.session.participants if p.actor_id is not None}
    if drivers.keys() != players.keys():
        raise ValueError("one independent driver is required for each player")
    if len({id(driver) for driver in drivers.values()}) != len(drivers):
        raise ValueError("players must not share a stateful driver instance")
    rules = SessionRules(scenario.session, RuinRules())
    repo = SQLiteWorldRepository(
        database_path, rules=rules, dice=FixedEffortDice(), seed=scenario.seed
    )
    repo.create_world(scenario.initial_state)
    channel = PartyChannel()
    adapters = {key: PlayerAdapter(repo, scenario.session, key, channel=channel) for key in players}
    previous = {}
    records = []
    for index in range(scenario.max_decisions):
        state = repo.get_state(scenario.session.world_id)
        if state.flags.get("gate_open") is True:
            break
        turn = turn_at(scenario.session, state.seq)
        active = turn.active_actor_id or scenario.session.turn_order[index % len(players)]
        participant_id = next(key for key, player in players.items() if player.actor_id == active)
        adapter = adapters[participant_id]
        observation = Observation(
            public_setup=scenario.public_setup,
            view=adapter.get_my_state(),
            events=adapter.get_recent_visible_events(),
            messages=adapter.get_party_messages(),
            previous_receipt=previous.get(participant_id),
        )
        # Drivers receive only a detached observation; mutable nested dictionaries cannot
        # rewrite the evidence record, another participant's input, or authoritative state.
        decision = drivers[participant_id].decide(observation.model_copy(deep=True))
        decision = AgentDecision.model_validate_json(decision.model_dump_json())
        proposal = ActionProposal(
            participant_id=participant_id,
            actor_id=active,
            expected_parent_seq=observation.view.seq,
            turn_revision=observation.view.turn.turn_revision,
            client_action_id=f"decision-{index + 1}",
            action=decision.proposed_action,
        )
        if decision.message_to_party is not None:
            adapter.send_party_message(decision.message_to_party)
        receipt = adapter.propose_action(proposal)
        previous[participant_id] = receipt
        records.append(
            DecisionRecord(
                decision_index=index + 1,
                participant_id=participant_id,
                observation=observation,
                decision=decision,
                proposal=proposal,
                receipt=receipt,
            )
        )
    journal = tuple(repo.entries(scenario.session.world_id))
    final = repo.get_state(scenario.session.world_id)
    # Reopen through the actual persistent path, with no configured dice needed for replay.
    reopened = SQLiteWorldRepository(
        database_path, rules=SessionRules(scenario.session, RuinRules())
    )
    if reopened.replay(scenario.session.world_id) != final:
        raise AssertionError("benchmark replay differs from committed final state")
    tools = tuple(
        ToolRecord(
            event_id=entry.committed.event.event_id or "",
            actor_id=entry.committed.event.actor_id,
            result=roll,
        )
        for entry in journal
        for roll in entry.result.rolls
    )
    accepted = sum(record.receipt.accepted for record in records)
    validator = validate_run(scenario, tuple(records), final)

    def tokens(field: str) -> int | None:
        counts = [getattr(record.decision, field) for record in records]
        return sum(counts) if all(count is not None for count in counts) else None

    return RunArtifact(
        scenario=scenario,
        runtime_version=RUNTIME_VERSION,
        effective_ruleset_id=rules.name,
        effective_ruleset_version=rules.version,
        source_sha256=source_hashes(),
        participant_models={key: driver.config for key, driver in drivers.items()},
        decisions=tuple(records),
        messages=tuple(channel.messages),
        journal=journal,
        tool_calls=tools,
        memory_retrievals=tuple(
            retrieval for adapter in adapters.values() for retrieval in adapter.memory_retrievals
        ),
        validator=validator,
        final_state=final,
        final_state_hash=stable_model_hash(final),
        metrics={
            "committed_turns": final.seq,
            "decision_attempts": len(records),
            "accepted_actions": accepted,
            "rejected_actions": len(records) - accepted,
            "invalid_action_rate": (len(records) - accepted) / len(records) if records else 0.0,
            "messages": len(channel.messages),
            "message_characters": sum(len(message.text) for message in channel.messages),
            "input_tokens": tokens("input_tokens"),
            "output_tokens": tokens("output_tokens"),
            "tool_calls": len(tools),
            "wait_actions": sum(
                r.proposal.action.kind == "wait" and r.receipt.accepted for r in records
            ),
        },
    )
