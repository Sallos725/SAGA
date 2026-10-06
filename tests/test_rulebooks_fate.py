import json

import pytest

from saga_trpg.domain.models import StatePatch
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
    assert (spec.id, spec.version) == ("fate-condensed", "0.4.0")
    assert set(spec.records) == {"fate_aspects", "fate_boosts"}
    assert spec.records["fate_aspects"].unique == ["name"]
    rules = fate_rules()
    assert set(rules.payload_models) == {"overcome", "create_advantage", "invoke", "use_boost"}
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
        (lambda d: d["records"]["fate_aspects"].update(unique=[]), "requires\\[0\\].unique.field"),
        (lambda d: _advantage(d)["on_outcome"].update(hit=[]), "on_outcome.hit"),
        (
            lambda d: _advantage(d)["on_outcome"]["success"].append(
                dict(_advantage(d)["on_outcome"]["success"][0])
            ),
            "fate_aspects twice",
        ),
        (
            lambda d: _advantage(d)["input"]["aspect_name"].update(type="actor", default=""),
            "aspect_name.default",
        ),
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
    latest = registry.get("fate-condensed", "0.3.0")
    assert isinstance(latest.rules, DataRules)
    assert isinstance(registry.get("fate-condensed", "0.1.0").rules, FateCondensedRules)
    assert latest.example_world("w") == fate_world("w")


def test_unique_rule_values_must_match_the_field_type(tmp_path):
    data = json.loads(FATE_PATH.read_text())
    _advantage(data)["requires"][0]["unique"]["value"] = "input.difficulty"
    dice = ScriptedDice()
    engine = engine_for(
        DataRules(load_rulebook(write_spec(tmp_path, data))), dice, state=fate_world("s")
    )
    with pytest.raises(ValueError, match="name"):
        engine.dispatch(event("create_advantage", {**ADVANTAGE}))
    assert dice.calls == []


# --- record builtins (fate 0.4.0) -----------------------------------------------------------


def fate_040():
    return DataRules(load_rulebook(rulebook_path("fate-condensed", "0.4.0")))


def aspect_world(*, invokes=2, boosts=()):
    state = fate_world("sample")
    state.flags["fate_aspects"]["a1"] = {
        "name": "Mapped route",
        "owner_id": "player",
        "free_invokes": invokes,
    }
    for index, name in enumerate(boosts):
        state.flags["fate_boosts"][f"b{index}"] = {"name": name, "owner_id": "player"}
    return state


def test_invoke_spends_one_free_invoke_without_dice_and_replays():
    dice = ScriptedDice()
    engine = engine_for(fate_040(), dice, state=aspect_world(invokes=2))
    initial = engine.state.model_copy(deep=True)
    result = engine.dispatch(event("invoke", {"name": "Mapped route"})).entry.result
    assert engine.state.flags["fate_aspects"]["a1"]["free_invokes"] == 1
    assert result.patches == [
        StatePatch(path="flags.fate_aspects.a1.free_invokes", before=2, after=1)
    ]
    assert result.rolls == [] and dice.calls == []
    engine.dispatch(event("invoke", {"name": "Mapped route", "amount": 1}, "i2"))
    assert engine.state.flags["fate_aspects"]["a1"]["free_invokes"] == 0
    with pytest.raises(ValueError, match="free_invokes"):
        engine.dispatch(event("invoke", {"name": "Mapped route"}, "i3"))
    with pytest.raises(ValueError, match="no fate_aspects"):
        engine.dispatch(event("invoke", {"name": "Unknown"}, "i4"))
    assert replay_recorded_results(initial, engine.journal.entries()) == engine.state


def test_use_boost_removes_the_record_and_rejects_unknown_names():
    engine = engine_for(fate_040(), ScriptedDice(), state=aspect_world(boosts=["Opening"]))
    result = engine.dispatch(event("use_boost", {"name": "Opening"})).entry.result
    assert engine.state.flags["fate_boosts"] == {}
    assert result.patches[0].path == "flags.fate_boosts.b0"
    assert result.patches[0].before["name"] == "Opening" and result.patches[0].after is None
    with pytest.raises(ValueError, match="no fate_boosts"):
        engine.dispatch(event("use_boost", {"name": "Opening"}, "again"))


def test_duplicate_unique_record_is_rejected_before_any_roll():
    dice = ScriptedDice()
    engine = engine_for(fate_040(), dice, state=aspect_world(boosts=["Temporary advantage"]))
    with pytest.raises(ValueError, match="Temporary advantage"):
        engine.dispatch(event("overcome", {"skill": "athletics", "difficulty": 0}))
    assert dice.calls == []
    dice = ScriptedDice(("4d3-8", [3, 3, 3, 3], -8))
    engine = engine_for(fate_040(), dice, state=aspect_world(boosts=["Temporary advantage"]))
    engine.dispatch(
        event("overcome", {"skill": "athletics", "difficulty": 0, "boost_name": "Other"})
    )
    assert {b["name"] for b in engine.state.flags["fate_boosts"].values()} == {
        "Temporary advantage",
        "Other",
    }


def test_record_commands_come_from_the_rulebook():
    from saga_trpg.rulebooks.commands import command_help, parse_command

    spec = load_rulebook(rulebook_path("fate-condensed", "0.4.0"))
    event_ = parse_command(spec, actor_id="player", text='invoke "Mapped route"')
    assert (event_.kind, event_.payload) == ("invoke", {"name": "Mapped route", "amount": 1})
    event_ = parse_command(spec, actor_id="player", text="use_boost Opening")
    assert (event_.kind, event_.payload) == ("use_boost", {"name": "Opening"})
    assert "invoke <name> [amount=…]" in command_help(spec)
    assert "use_boost <name>" in command_help(spec)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda d: d["actions"]["invoke"].update(record="ghosts"), "invoke.record"),
        (lambda d: d["actions"]["invoke"].update(field="name"), "invoke.field"),
        (lambda d: d["records"]["fate_boosts"].update(unique=[]), "use_boost.record"),
        (lambda d: d["actions"]["invoke"].pop("sign"), "invoke.sign"),
    ],
)
def test_record_builtins_are_validated_by_entry(tmp_path, mutate, message):
    data = json.loads(rulebook_path("fate-condensed", "0.4.0").read_text())
    mutate(data)
    with pytest.raises(RulebookError, match=message):
        load_rulebook(write_spec(tmp_path, data))


def test_record_keys_keep_their_declared_type_and_enum_spelling(tmp_path):
    data = json.loads(rulebook_path("fate-condensed", "0.4.0").read_text())
    data["records"]["clocks"] = {
        "fields": {
            "kind": {"type": "text", "enum": ["Open", "Closed"]},
            "ticks": {"type": "integer", "range": [0, 8]},
        },
        "unique": ["kind"],
    }
    data["records"]["flags"] = {
        "fields": {"armed": {"type": "boolean"}, "count": {"type": "integer", "range": [0, 9]}},
        "unique": ["armed"],
    }
    data["actions"]["tick"] = {
        "builtin": "record_adjust",
        "record": "clocks",
        "field": "ticks",
        "sign": 1,
    }
    data["actions"]["bump"] = {
        "builtin": "record_adjust",
        "record": "flags",
        "field": "count",
        "sign": 1,
    }
    spec = load_rulebook(write_spec(tmp_path, data))
    from saga_trpg.rulebooks.commands import parse_command

    assert parse_command(spec, actor_id="player", text="tick open").payload == {
        "kind": "Open",
        "amount": 1,
    }
    assert parse_command(spec, actor_id="player", text="bump true 2").payload == {
        "armed": True,
        "amount": 2,
    }
    state = fate_world("s")
    state.flags["clocks"] = {"c1": {"kind": "Open", "ticks": 0}}
    state.flags["flags"] = {"f1": {"armed": True, "count": 0}}
    engine = engine_for(DataRules(spec), ScriptedDice(), state=state)
    engine.dispatch(event("tick", {"kind": "Open"}))
    engine.dispatch(event("bump", {"armed": True}, "b"))
    assert engine.state.flags["clocks"]["c1"]["ticks"] == 1
    assert engine.state.flags["flags"]["f1"]["count"] == 1


def test_record_builtins_refuse_ambiguous_or_malformed_collections_and_unique_collisions(
    tmp_path,
):
    data = json.loads(rulebook_path("fate-condensed", "0.4.0").read_text())
    data["records"]["counters"] = {
        "fields": {"value": {"type": "integer", "range": [0, 9]}},
        "unique": ["value"],
    }
    data["actions"]["grow"] = {
        "builtin": "record_adjust",
        "record": "counters",
        "field": "value",
        "sign": 1,
    }
    spec = load_rulebook(write_spec(tmp_path, data))
    state = fate_world("s")
    state.flags["counters"] = {"a": {"value": 1}, "b": {"value": 2}}
    engine = engine_for(DataRules(spec), ScriptedDice(), state=state)
    with pytest.raises(ValueError, match="already"):
        engine.dispatch(event("grow", {"value": 1}))
    state = fate_world("s")
    state.flags["fate_boosts"] = {
        "x": {"name": "Dup", "owner_id": "player"},
        "y": {"name": "Dup", "owner_id": "player"},
    }
    engine = engine_for(DataRules(spec), ScriptedDice(), state=state)
    with pytest.raises(ValueError, match="more than one"):
        engine.dispatch(event("use_boost", {"name": "Dup"}))
    state = fate_world("s")
    state.flags["fate_boosts"] = ["not", "a", "dict"]
    engine = engine_for(DataRules(spec), ScriptedDice(), state=state)
    with pytest.raises(ValueError, match="fate_boosts"):
        engine.dispatch(event("use_boost", {"name": "Dup"}))
