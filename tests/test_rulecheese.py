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
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(dict(SCENARIO, ruleset_version="9.9.9")))
    with pytest.raises(ValueError, match="9.9.9"):
        load_scenario(bad)
    with pytest.raises(ValueError):
        BenchmarkScenario.model_validate(
            dict(SCENARIO, validators=[{"id": "x", "kind": "telepathy"}])
        )
    with pytest.raises(ValueError, match="missing"):
        BenchmarkScenario.model_validate(dict(SCENARIO, success=["missing"]))


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


@pytest.mark.asyncio
async def test_attempt_budget_bounds_endless_illegal_commands_and_is_observable():
    class Stubborn:
        def __init__(self):
            self.seen = []

        async def decide(self, observation):
            self.seen.append(observation.attempts_left)
            return "dance"

    driver = Stubborn()
    run = await RuleCheeseRunner().run(scenario(max_turns=3), driver=driver, model="x")
    assert run.metrics["attempts"] == 6 and run.metrics["turns"] == 0
    assert driver.seen[0] == 6 and driver.seen[-1] == 1
    # No failure validator fired and no success validator passed: unscored, not success.
    assert run.outcome == "partial" and run.validators["no_illegal"] is False
    custom = await RuleCheeseRunner().run(
        scenario(max_turns=3, max_attempts=4), driver=Stubborn(), model="x"
    )
    assert custom.metrics["attempts"] == 4


@pytest.mark.asyncio
async def test_a_command_literally_named_tape_is_an_ordinary_rejection():
    tape = {"mode": "tape", "rolls": {"1d20": [[15]], "1d8+3": [[4]]}}
    run = await RuleCheeseRunner().run(
        scenario(stochastic=tape), driver=ScriptedDriver(["tape", "attack guard"]), model="x"
    )
    assert run.metrics["illegal_attempts"] == 1 and run.metrics["turns"] == 1


@pytest.mark.asyncio
async def test_no_success_criteria_is_unscored_not_success():
    run = await RuleCheeseRunner().run(
        scenario(success=[]), driver=ScriptedDriver(["attack guard"]), model="x"
    )
    assert run.outcome == "partial"
    assert any("no success criteria" in note for note in run.notes)


def test_state_validator_distinguishes_missing_paths_from_stored_null():
    from saga_trpg.benchmark.models import StateValidator
    from saga_trpg.benchmark.validators import evaluate_validators
    from saga_trpg.domain.models import WorldState

    state = WorldState(world_id="w", flags={"present": None})
    verdicts = evaluate_validators(
        [
            StateValidator(id="present", kind="state", path="flags.present", equals=None),
            StateValidator(id="missing", kind="state", path="flags.typo", equals=None),
        ],
        state=state,
        entries=[],
        illegal_attempts=0,
    )
    assert verdicts == {"present": True, "missing": False}


def test_scenarios_validate_against_the_runner_registry_including_imports(tmp_path):
    import json as _json

    from saga_trpg.benchmark.models import validate_scenario
    from saga_trpg.modules.catalog import RulesetRegistry, rulebook_definition
    from saga_trpg.rulebooks.loader import parse_rulebook, rulebook_path

    data = _json.loads(rulebook_path("fate-condensed").read_text())
    data["id"], data["version"] = "house-fate", "1.0.0"
    registry = RulesetRegistry()
    registry.add(rulebook_definition(parse_rulebook(data)))
    spec = BenchmarkScenario.model_validate(
        dict(
            SCENARIO,
            ruleset_id="house-fate",
            ruleset_version="1.0.0",
            validators=[],
            success=[],
            failure=[],
        )
    )
    with pytest.raises(ValueError, match="house-fate"):
        validate_scenario(spec, RulesetRegistry())
    validate_scenario(spec, registry)


# --- model drivers ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_driver_formats_public_observation_and_parses_one_command():
    from saga_trpg.benchmark.drivers import PROMPT_TEMPLATE_VERSION, PromptDriver

    prompts = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "Thinking about it...\n\n```\nattack guard\n```\n"

    driver = PromptDriver(complete, name="fake-model")
    run = await RuleCheeseRunner().run(scenario(max_turns=1), driver=driver, model="fake")
    assert run.metrics["turns"] == 1 and run.event_ids == ["turn-1"]
    prompt = prompts[0]
    assert "A guard blocks the west gate" in prompt
    assert "attack <target_id>" in prompt and "Turn 1 of 1" in prompt
    assert "guard" in prompt and "validators" not in prompt and "guard_down" not in prompt
    assert run.driver == {
        "name": "fake-model",
        "template": PROMPT_TEMPLATE_VERSION,
        "calls": 1,
        "stopped_early": False,
    }


@pytest.mark.asyncio
async def test_prompt_driver_shows_the_last_error_and_can_stop():
    from saga_trpg.benchmark.drivers import PromptDriver

    answers = iter(["dance", "attack guard", "END"])
    prompts = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    run = await RuleCheeseRunner().run(
        scenario(max_turns=5), driver=PromptDriver(complete, name="m"), model="m"
    )
    assert run.metrics["illegal_attempts"] == 1 and run.metrics["turns"] == 1
    assert "unknown action 'dance'" in prompts[1]
    assert run.driver["stopped_early"] is True and run.driver["calls"] == 3


@pytest.mark.asyncio
async def test_vertex_driver_uses_the_narrator_transport(monkeypatch):
    import json as _json
    from types import SimpleNamespace

    import httpx
    from google.oauth2 import service_account
    from pydantic import SecretStr

    from saga_trpg.benchmark.drivers import VertexDriver

    monkeypatch.setattr(
        service_account.Credentials,
        "from_service_account_info",
        lambda info, *, scopes: SimpleNamespace(token="tok", refresh=lambda transport: None),
    )
    seen = []

    def remote(req):
        seen.append(_json.loads(req.content))
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "attack guard"}]}}]}
        )

    account = {
        "type": "service_account",
        "project_id": "saga-test-project",
        "client_email": "bench@saga-test-project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    driver = VertexDriver(SecretStr(_json.dumps(account)), transport=httpx.MockTransport(remote))
    run = await RuleCheeseRunner().run(scenario(max_turns=1), driver=driver, model="gemini")
    assert run.metrics["turns"] == 1
    body = seen[0]
    assert "one command" in body["systemInstruction"]["parts"][0]["text"].lower()
    assert "attack <target_id>" in body["contents"][-1]["parts"][0]["text"]
    assert run.driver["name"].startswith("vertex:")


def test_prompt_driver_module_imports_without_provider_extras(monkeypatch):
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("httpx", "google")):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    for module in [m for m in sys.modules if m.startswith("saga_trpg.benchmark.drivers")]:
        del sys.modules[module]
    monkeypatch.setattr(builtins, "__import__", guarded)
    drivers = importlib.import_module("saga_trpg.benchmark.drivers")
    assert drivers.PromptDriver


@pytest.mark.asyncio
async def test_vertex_driver_failures_become_a_sanitized_value_error(monkeypatch):
    import json as _json
    from types import SimpleNamespace

    import httpx
    from google.oauth2 import service_account
    from pydantic import SecretStr

    from saga_trpg.benchmark.drivers import VertexDriver

    monkeypatch.setattr(
        service_account.Credentials,
        "from_service_account_info",
        lambda info, *, scopes: SimpleNamespace(token="tok", refresh=lambda transport: None),
    )

    def remote(req):
        raise httpx.ReadTimeout("slow", request=req)

    account = {
        "type": "service_account",
        "project_id": "saga-test-project",
        "client_email": "bench@saga-test-project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nSECRETKEY\n-----END PRIVATE KEY-----\n",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    driver = VertexDriver(SecretStr(_json.dumps(account)), transport=httpx.MockTransport(remote))
    with pytest.raises(ValueError, match="Vertex") as info:
        await RuleCheeseRunner().run(scenario(max_turns=1), driver=driver, model="g")
    assert "SECRETKEY" not in str(info.value) and "tok" not in str(info.value)


# --- reports ---------------------------------------------------------------------------------


async def _two_runs(tmp_path):
    runner = RuleCheeseRunner()
    good = await runner.run(
        scenario(),
        driver=ScriptedDriver(
            ["inflict guard prone", "attack guard", "attack guard", "attack guard"]
        ),
        model="careful",
    )
    sloppy = await runner.run(
        scenario(),
        driver=ScriptedDriver(["dance", "heal player 1", "attack guard"]),
        model="sloppy",
    )
    paths = []
    for run in (good, sloppy):
        path = tmp_path / f"{run.model}.json"
        path.write_text(run.model_dump_json())
        paths.append(path)
    return good, sloppy, paths


@pytest.mark.asyncio
async def test_report_compares_runs_of_one_scenario_and_refuses_incomparable_ones(tmp_path):
    from saga_trpg.benchmark.report import IncomparableRuns, compare_runs, render_report

    good, sloppy, paths = await _two_runs(tmp_path)
    rows = compare_runs([good, sloppy])
    assert [row.model for row in rows] == ["careful", "sloppy"]
    assert rows[0].outcome == "success" and rows[1].outcome == "failure"
    assert rows[1].illegal_attempts == 1
    text = render_report([good, sloppy])
    assert "| careful |" in text and "| sloppy |" in text
    assert "guard_down" in text and "drop-the-guard" in text
    assert "bench-seed-1" in text and good.runtime_version in text
    other = good.model_copy(update={"ruleset_version": "0.4.0"})
    with pytest.raises(IncomparableRuns, match="ruleset_version"):
        compare_runs([good, other])
    other = good.model_copy(update={"stochastic": {"mode": "seed", "seed": "else"}})
    with pytest.raises(IncomparableRuns, match="stochastic"):
        compare_runs([good, other])


@pytest.mark.asyncio
async def test_report_cli_prints_a_table_and_writes_markdown(tmp_path, capsys):
    from saga_trpg.benchmark.__main__ import main

    _, _, paths = await _two_runs(tmp_path)
    out = tmp_path / "report.md"
    assert main(["report", *map(str, paths), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "careful" in printed and "sloppy" in printed
    assert out.read_text().startswith("# RuleCheeseBench")
    bad = tmp_path / "bad.json"
    bad.write_text(
        paths[0].read_text().replace('"ruleset_version":"0.5.0"', '"ruleset_version":"0.4.0"')
    )
    assert main(["report", str(paths[0]), str(bad)]) == 1
    assert "ruleset_version" in capsys.readouterr().err
