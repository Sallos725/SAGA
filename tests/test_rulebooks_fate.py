import json

import pytest

from saga_trpg.modules.fate_condensed.rules import FateCondensedRules
from saga_trpg.modules.fate_condensed.rules import example_world as fate_world
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import RulebookError, load_rulebook, rulebook_path
from saga_trpg.runtime.replay import replay_recorded_results
from tests.test_rulebooks import ScriptedDice, engine_for, event, write_spec

FATE_PATH = rulebook_path("fate-condensed")


def fate_rules():
    return DataRules(load_rulebook(FATE_PATH))


def test_fate_rulebook_loads_with_records_and_graded_outcomes():
    spec = load_rulebook(FATE_PATH)
    assert (spec.id, spec.version) == ("fate-condensed", "0.2.0")
    assert set(spec.records) == {"fate_aspects", "fate_boosts"}
    assert spec.records["fate_aspects"].unique == ["name"]
    rules = fate_rules()
    assert set(rules.payload_models) == {"overcome", "create_advantage"}
    schema = rules.payload_models["overcome"].model_json_schema()
    assert schema["properties"]["boost_name"]["default"] == "Temporary advantage"
    assert "advantage" not in schema["properties"]


OVERCOME = {"skill": "investigate", "difficulty": 2}
ADVANTAGE = {"skill": "notice", "difficulty": 1, "aspect_name": "Mapped route"}
SCENARIOS = [
    ("overcome", OVERCOME, [1, 1, 1, 1], "failure", True),
    ("overcome", OVERCOME, [1, 1, 3, 3], "tie", True),
    ("overcome", OVERCOME, [1, 2, 3, 3], "success", False),
    ("overcome", OVERCOME, [2, 3, 3, 3], "success_with_style", False),
    ("overcome", {**OVERCOME, "boost_name": "Opening"}, [3, 3, 3, 3], "success_with_style", False),
    ("create_advantage", ADVANTAGE, [1, 1, 1, 1], "failure", True),
    ("create_advantage", ADVANTAGE, [1, 1, 2, 2], "tie", False),
    ("create_advantage", ADVANTAGE, [1, 2, 2, 3], "success", False),
    ("create_advantage", ADVANTAGE, [3, 3, 3, 3], "success_with_style", False),
]


def normalized_rolls(result):
    return [roll.model_dump(exclude={"roll_key"}) for roll in result.rolls]


@pytest.mark.parametrize("kind,payload,raw,outcome,pending", SCENARIOS)
def test_data_engine_matches_python_fate_010(kind, payload, raw, outcome, pending):
    results = []
    for rules in (FateCondensedRules(), fate_rules()):
        dice = ScriptedDice(("4d3-8", raw, -8))
        engine = engine_for(rules, dice, state=fate_world("sample"))
        results.append(engine.dispatch(event(kind, payload)).entry.result)
        assert dice.remaining == []
    python, data = results
    assert data.resolution.outcome == outcome
    assert data.resolution.pending_adjudication is pending
    assert data.state == python.state
    assert data.patches == python.patches
    assert data.resolution == python.resolution
    # Roll keys follow the generic `<kind>:<index>` convention; everything else is identical.
    assert normalized_rolls(data) == normalized_rolls(python)


def test_fate_records_survive_replay_and_regeneration():
    dice = ScriptedDice(("4d3-8", [3, 3, 3, 3], -8), ("4d3-8", [2, 3, 3, 3], -8))
    engine = engine_for(fate_rules(), dice, state=fate_world("sample"))
    initial = engine.state.model_copy(deep=True)
    engine.dispatch(
        event("create_advantage", {"skill": "notice", "difficulty": 1, "aspect_name": "Mapped"})
    )
    engine.dispatch(event("overcome", {"skill": "athletics", "difficulty": 0}, "o"))
    assert engine.state.flags["fate_aspects"]["action-1"] == {
        "name": "Mapped",
        "owner_id": "player",
        "free_invokes": 2,
    }
    assert engine.state.flags["fate_boosts"]["o"]["name"] == "Temporary advantage"
    assert replay_recorded_results(initial, engine.journal.entries()) == engine.state
    assert engine.dispatch(event("overcome", {"skill": "athletics", "difficulty": 0}, "o")).replayed
    assert len(dice.calls) == 2


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("overcome", {"skill": "missing", "difficulty": 2}),
        ("overcome", {"skill": "investigate", "difficulty": 2, "bonus": 999}),
        ("overcome", {"skill": "investigate", "difficulty": 99}),
        ("attack", {"target_id": "opponent"}),
        ("create_advantage", {"skill": "notice", "difficulty": 1}),
    ],
)
def test_fate_rejections_happen_before_dice(kind, payload):
    dice = ScriptedDice()
    engine = engine_for(fate_rules(), dice, state=fate_world("sample"))
    with pytest.raises(ValueError):
        engine.dispatch(event(kind, payload))
    assert dice.calls == [] and engine.journal.entries() == []


def test_existing_aspect_name_is_rejected_by_the_unique_rule_before_dice():
    dice = ScriptedDice(("4d3-8", [3, 3, 3, 3], -8))
    engine = engine_for(fate_rules(), dice, state=fate_world("sample"))
    payload = {"skill": "investigate", "difficulty": 2, "aspect_name": "Mapped route"}
    engine.dispatch(event("create_advantage", payload))
    with pytest.raises(ValueError, match="Mapped route"):
        engine.dispatch(event("create_advantage", payload, "again"))
    assert len(dice.calls) == 1


def test_malformed_existing_records_are_rejected_before_dice():
    dice = ScriptedDice()
    engine = engine_for(fate_rules(), dice, state=fate_world("sample"))
    engine.state.flags["fate_aspects"]["x"] = {"name": "Broken"}
    with pytest.raises(ValueError, match="fate_aspects"):
        engine.dispatch(event("overcome", {"skill": "investigate", "difficulty": 2}))
    assert dice.calls == []


def _advantage(d):
    return d["actions"]["create_advantage"]


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda d: _advantage(d)["on_outcome"].update(win=[]), "on_outcome.win"),
        (lambda d: _advantage(d)["on_outcome"]["success"][0].update(record="nope"), "nope"),
        (lambda d: _advantage(d)["on_outcome"]["success"][0]["fields"].pop("owner_id"), "owner_id"),
        (lambda d: _advantage(d)["requires"][0]["unique"].update(field="colour"), "colour"),
        (lambda d: d["records"]["fate_aspects"].update(unique=["size"]), "size"),
    ],
)
def test_record_and_outcome_effects_are_validated_by_entry(tmp_path, mutate, message):
    data = json.loads(FATE_PATH.read_text())
    mutate(data)
    with pytest.raises(RulebookError, match=message):
        load_rulebook(write_spec(tmp_path, data))


def test_registry_serves_fate_020_from_the_rulebook():
    from saga_trpg.modules.catalog import RulesetRegistry

    registry = RulesetRegistry()
    latest = registry.get("fate-condensed", "0.2.0")
    assert isinstance(latest.rules, DataRules)
    assert isinstance(registry.get("fate-condensed", "0.1.0").rules, FateCondensedRules)
    assert latest.example_world("w") == fate_world("w")
