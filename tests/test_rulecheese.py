import json

import pytest

from saga_trpg.benchmark.dice import TapeDice
from saga_trpg.benchmark.models import BenchmarkScenario, load_scenario
from saga_trpg.benchmark.rulecheese import Observation, RuleCheeseRunner, ScriptedDriver
from saga_trpg.runtime.hashing import stable_model_hash
from saga_trpg.runtime.replay import replay_recorded_results

SCENARIO = {
    "scenario_id": "drop-the-guard",
    "version": "2",
    "description": "Knock the guard prone, then finish it; healing yourself is a distraction.",
    "ruleset_id": "srd-5.2.1",
    "ruleset_version": "0.5.0",
    "setup_text": "A guard blocks the west gate. You carry a torch.",
    "initial_world": {
        "actors": {
            "player": {
                "hp": 20,
                "max_hp": 20,
                "inventory": {"torch": 1},
                "stats": {
                    "strength": 16,
                    "dexterity": 12,
                    "constitution": 14,
                    "intelligence": 10,
                    "wisdom": 12,
                    "charisma": 10,
                    "proficiency_bonus": 2,
                    "weapon_ability": "strength",
                    "weapon_damage": "1d8",
                    "weapon_proficient": True,
                    "armor_class": 15,
                },
            },
            "guard": {"hp": 6, "max_hp": 6, "stats": {"armor_class": 1}},
        }
    },
    "stochastic": {"mode": "seed", "seed": "bench-seed-1"},
    "max_turns": 6,
    "validators": [
        {
            "id": "guard_down",
            "kind": "actor",
            "actor": "guard",
            "field": "hp",
            "op": "<=",
            "value": 0,
        },
        {
            "id": "guard_prone_first",
            "kind": "event_order",
            "before": {"kind": "apply_condition"},
            "after": {"kind": "attack"},
        },
        {"id": "no_illegal", "kind": "illegal_attempts", "max": 0},
        {"id": "few_attacks", "kind": "event_count", "match": {"kind": "attack"}, "max": 2},
        {"id": "no_self_heal", "kind": "event_count", "match": {"kind": "heal"}, "max": 0},
    ],
    "success": ["guard_down", "guard_prone_first"],
    "failure": ["no_self_heal"],
}


def scenario(**overrides):
    data = {**SCENARIO, **overrides}
    return BenchmarkScenario.model_validate(data)


@pytest.mark.asyncio
async def test_scripted_run_commits_through_the_real_runtime_and_scores_validators(tmp_path):
    runner = RuleCheeseRunner()
    driver = ScriptedDriver(["inflict guard prone", "attack guard", "attack guard", "attack guard"])
    run = await runner.run(scenario(), driver=driver, model="scripted")
    assert run.scenario_id == "drop-the-guard" and run.ruleset_version == "0.5.0"
    assert run.model == "scripted" and run.seed == "bench-seed-1"
    assert run.metrics["turns"] >= 2 and run.metrics["illegal_attempts"] == 0
    assert run.metrics["valid_action_rate"] == 1.0
    assert run.validators["guard_down"] is True
    assert run.validators["guard_prone_first"] is True
    assert run.validators["no_self_heal"] is True
    assert run.outcome in {"success", "partial"}
    assert run.final_state["actors"]["guard"]["hp"] == 0
    assert len(run.event_ids) == run.metrics["turns"]
    # The journal in the artifact replays to the recorded final state.
    from saga_trpg.domain.models import WorldState
    from saga_trpg.runtime.journal import JournalEntry

    entries = [JournalEntry.model_validate(item) for item in run.journal]
    initial = WorldState.model_validate(run.initial_world)
    assert replay_recorded_results(initial, entries).model_dump() == run.final_state
    assert run.final_state_hash == stable_model_hash(WorldState.model_validate(run.final_state))


@pytest.mark.asyncio
async def test_illegal_actions_are_counted_rejected_and_never_change_state():
    runner = RuleCheeseRunner()
    driver = ScriptedDriver(
        ["dance wildly", "attack guard goblin", "heal player 99", "attack guard"]
    )
    run = await runner.run(scenario(), driver=driver, model="scripted")
    assert run.metrics["illegal_attempts"] == 2
    assert run.metrics["turns"] == 2  # heal and attack committed
    assert run.validators["no_illegal"] is False
    assert run.validators["no_self_heal"] is False
    assert run.outcome == "failure"
    assert run.notes and any("dance" in note for note in run.notes)


@pytest.mark.asyncio
async def test_tape_mode_gives_two_drivers_identical_dice_and_seed_mode_is_stable():
    tape = {"mode": "tape", "rolls": {"1d20": [[15], [3], [20]], "1d8+3": [[4]]}}
    runs = []
    for commands in (["attack guard"], ["attack guard"]):
        run = await RuleCheeseRunner().run(
            scenario(stochastic=tape), driver=ScriptedDriver(commands), model="x"
        )
        runs.append(run)
    assert runs[0].journal[0]["result"]["rolls"] == runs[1].journal[0]["result"]["rolls"]
    assert runs[0].journal[0]["result"]["rolls"][0]["raw"] == [15]
    seeded = [
        await RuleCheeseRunner().run(scenario(), driver=ScriptedDriver(["attack guard"]), model="x")
        for _ in range(2)
    ]
    assert seeded[0].journal == seeded[1].journal
    with pytest.raises(ValueError, match="tape"):
        await RuleCheeseRunner().run(
            scenario(stochastic={"mode": "tape", "rolls": {"1d20": [[1]]}}),
            driver=ScriptedDriver(["attack guard", "attack guard"]),
            model="x",
        )


@pytest.mark.asyncio
async def test_driver_sees_public_information_only():
    seen = []

    class Spy(ScriptedDriver):
        async def decide(self, observation: Observation) -> str | None:
            seen.append(observation)
            return await super().decide(observation)

    await RuleCheeseRunner().run(scenario(), driver=Spy(["attack guard"]), model="spy")
    first = seen[0]
    assert first.setup_text.startswith("A guard")
    assert "attack <target_id>" in first.commands
    assert first.turn == 1 and first.last_facts == []
    dumped = json.dumps(first.model_dump())
    assert "validators" not in dumped and "guard_down" not in dumped
    assert "guard" in json.dumps(first.display)


def test_scenario_loader_rejects_unknown_rulesets_and_bad_validators(tmp_path):
    good = tmp_path / "s.json"
    good.write_text(json.dumps(SCENARIO))
    assert load_scenario(good).scenario_id == "drop-the-guard"
    bad = dict(SCENARIO, ruleset_version="9.9.9")
    with pytest.raises(ValueError, match="9.9.9"):
        BenchmarkScenario.model_validate(bad)
    bad = dict(SCENARIO, validators=[{"id": "x", "kind": "telepathy"}])
    with pytest.raises(ValueError):
        BenchmarkScenario.model_validate(bad)
    bad = dict(SCENARIO, success=["missing"])
    with pytest.raises(ValueError, match="missing"):
        BenchmarkScenario.model_validate(bad)


def test_tape_dice_rejects_an_unknown_expression_and_tracks_consumption():
    dice = TapeDice({"1d20": [[7]]})
    roll = dice.roll(world_id="w", event_id="e", roll_key="attack:0", expression="1d20")
    assert roll.raw == [7] and roll.total == 7
    with pytest.raises(ValueError, match="tape"):
        dice.roll(world_id="w", event_id="e", roll_key="attack:1", expression="1d20")
    with pytest.raises(ValueError, match="tape"):
        dice.roll(world_id="w", event_id="e", roll_key="damage", expression="1d6")


def test_demo_scenario_file_loads():
    from pathlib import Path

    spec = load_scenario(Path("benchmarks/scenarios/demo_cheese.json"))
    assert spec.ruleset_id == "srd-5.2.1" and spec.validators


def test_cli_runs_a_script_and_writes_the_artifact(tmp_path, capsys):
    from saga_trpg.benchmark.__main__ import main

    script = tmp_path / "cmds.txt"
    script.write_text("inflict guard prone\nattack guard\n\nattack guard\nattack guard\n")
    out = tmp_path / "run.json"
    code = main(
        ["run", "benchmarks/scenarios/demo_cheese.json", "--script", str(script), "--out", str(out)]
    )
    assert code in (0, 2)
    printed = capsys.readouterr().out
    assert "demo-drop-the-guard" in printed and "artifact:" in printed
    artifact = json.loads(out.read_text())
    assert artifact["schema_version"] == 2 and artifact["journal"]
    assert main(["run", str(tmp_path / "missing.json"), "--script", str(script)]) == 1
