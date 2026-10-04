from pathlib import Path

import pytest

from saga_trpg.benchmark.multiagent.drivers import ScriptedDriver
from saga_trpg.benchmark.multiagent.models import AgentDecision, DriverConfig, RunArtifact
from saga_trpg.benchmark.multiagent.runner import load_scenario, run_scenario
from saga_trpg.experimental.multiplayer.models import ProposedAction

SCENARIOS = Path(__file__).resolve().parents[1] / "benchmarks" / "multiagent"


def drivers(strategy="cooperative"):
    return {key: ScriptedDriver(strategy) for key in ("human-a", "ai-b", "ai-c")}


@pytest.mark.parametrize(
    "scenario_id", ["distributed-knowledge", "cooperative-cheese", "bad-teammate"]
)
def test_scripted_cooperation_and_identical_reproduction(tmp_path, scenario_id):
    scenario = load_scenario(SCENARIOS / f"{scenario_id}.json")
    party = drivers()
    if scenario_id == "bad-teammate":
        party["human-a"] = ScriptedDriver("bad_teammate")
    first = run_scenario(scenario, party, tmp_path / "one.sqlite3")
    second_party = drivers()
    if scenario_id == "bad-teammate":
        second_party["human-a"] = ScriptedDriver("bad_teammate")
    second = run_scenario(scenario, second_party, tmp_path / "two.sqlite3")
    assert first == second
    assert first.validator.legal_success
    assert first.validator.hidden_actor_leaks == 0
    assert RunArtifact.model_validate_json(first.model_dump_json()) == first
    if scenario_id == "bad-teammate":
        assert first.validator.recovered_after_rejection
        assert first.validator.invalid_resource_attempts == 1
        assert any("Use only an owned silver key" in message.text for message in first.messages)


def test_only_strategy_changes_efficiency_under_identical_stochastic_conditions(tmp_path):
    scenario = load_scenario(SCENARIOS / "cooperative-cheese.json")
    cooperative = run_scenario(scenario, drivers(), tmp_path / "combo.sqlite3")
    brute = run_scenario(scenario, drivers("brute_force"), tmp_path / "brute.sqlite3")
    assert cooperative.scenario == brute.scenario
    assert cooperative.validator.legal_success and brute.validator.legal_success
    assert cooperative.validator.cheese_class == "C2"
    assert brute.validator.cheese_class == "C0"
    assert len(cooperative.journal) == 4 < len(brute.journal) == 7
    assert all(call.result.total == 2 for run in (cooperative, brute) for call in run.tool_calls)


def test_knowledge_cheating_is_not_rewarded_even_if_mechanically_successful(tmp_path):
    scenario = load_scenario(SCENARIOS / "distributed-knowledge.json")

    class CheatingDriver:
        config = DriverConfig(strategy="deliberately-cheating-test")

        def decide(self, observation):
            # Deliberately inject judge facts that are absent from this actor's observation.
            assert "DAWN" not in observation.model_dump_json()
            assert "STEP_LEFT" not in observation.model_dump_json()
            return AgentDecision(
                proposed_action=ProposedAction(
                    kind="unlock",
                    payload={
                        "item": "silver_key",
                        "inscription": "DAWN",
                        "warning": "STEP_LEFT",
                    },
                )
            )

    party = drivers("silent")
    party["ai-b"] = CheatingDriver()
    run = run_scenario(scenario, party, tmp_path / "cheat.sqlite3")
    assert run.validator.objective_completed
    assert run.validator.forbidden_knowledge_uses == 2
    assert not run.validator.legal_success
    assert run.validator.cheese_class == "INVALID"


def test_silent_party_cannot_complete_distributed_knowledge(tmp_path):
    scenario = load_scenario(SCENARIOS / "distributed-knowledge.json")
    run = run_scenario(scenario, drivers("silent"), tmp_path / "silent.sqlite3")
    assert not run.validator.objective_completed
    assert run.validator.omitted_clues == 2
    assert len(run.decisions) == scenario.max_decisions


def test_driver_can_neither_mutate_evidence_nor_receive_judge_state(tmp_path):
    scenario = load_scenario(SCENARIOS / "distributed-knowledge.json")

    class MutatingDriver(ScriptedDriver):
        def decide(self, observation):
            assert "VALIDATOR_CANARY" not in observation.model_dump_json()
            assert "hidden_checks" not in observation.model_dump_json()
            decision = super().decide(observation)
            observation.view.own_inventory["invented"] = 100
            observation.view.public_flags["gate_open"] = True
            return decision

    party = {key: MutatingDriver() for key in ("human-a", "ai-b", "ai-c")}
    run = run_scenario(scenario, party, tmp_path / "mutating.sqlite3")
    assert run.validator.legal_success
    assert "invented" not in run.model_dump_json()
    assert not run.decisions[0].observation.view.public_flags["gate_open"]


def test_hidden_leak_validator_detects_corrupted_observation(tmp_path):
    from saga_trpg.benchmark.multiagent.validators import validate_run

    scenario = load_scenario(SCENARIOS / "distributed-knowledge.json")
    run = run_scenario(scenario, drivers(), tmp_path / "leak.sqlite3")
    record = run.decisions[0]
    corrupted = record.model_copy(
        update={
            "observation": record.observation.model_copy(
                update={"public_setup": "hidden-custodian"}
            )
        }
    )
    verdict = validate_run(scenario, (corrupted, *run.decisions[1:]), run.final_state)
    assert verdict.hidden_actor_leaks == 1
    assert not verdict.legal_success


def test_cli_writes_reopenable_artifact_and_preserves_existing_outputs(tmp_path):
    import subprocess
    import sys

    output = tmp_path / "run.json"
    database = tmp_path / "run.sqlite3"
    command = [
        sys.executable,
        "-m",
        "saga_trpg.benchmark.multiagent",
        "--scenario",
        str(SCENARIOS / "cooperative-cheese.json"),
        "--output",
        str(output),
        "--database",
        str(database),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    saved = output.read_bytes()
    assert RunArtifact.model_validate_json(saved).validator.legal_success
    repeated = subprocess.run(command, capture_output=True, text=True)
    assert repeated.returncode == 2
    assert output.read_bytes() == saved
