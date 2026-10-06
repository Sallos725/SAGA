import json

import pytest

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import RollRecord
from saga_trpg.modules.srd_5_2_1.rules import SRD521Rules
from saga_trpg.modules.srd_5_2_1.rules import example_world as srd_world
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import RulebookError, load_rulebook, rulebook_path
from saga_trpg.runtime.engine import RuntimeEngine
from saga_trpg.runtime.journal import InMemoryJournal
from saga_trpg.runtime.replay import replay_recorded_results

SRD_PATH = rulebook_path("srd-5.2.1")


class ScriptedDice:
    def __init__(self, *rolls):
        self.remaining = list(rolls)
        self.calls = []

    def roll(self, *, world_id, event_id, roll_key, expression):
        self.calls.append((roll_key, expression))
        expected_expression, raw, modifier = self.remaining.pop(0)
        assert expression == expected_expression, (expression, expected_expression)
        return RollRecord(
            roll_key=roll_key,
            expression=expression,
            raw=raw,
            modifier=modifier,
            total=sum(raw) + modifier,
        )


def event(kind, payload, event_id="action-1", actor_id="player"):
    return GameEvent(event_id=event_id, kind=kind, actor_id=actor_id, payload=payload)


def engine_for(rules, dice, state=None):
    return RuntimeEngine(
        initial_state=state or srd_world("sample"),
        rules=rules,
        dice=dice,
        journal=InMemoryJournal(),
    )


def spec_dict():
    return json.loads(SRD_PATH.read_text())


def write_spec(tmp_path, data):
    path = tmp_path / "book.json"
    path.write_text(json.dumps(data))
    return path


# --- loader and validation -----------------------------------------------------------------


def test_srd_rulebook_loads_and_exposes_closed_payload_models():
    spec = load_rulebook(SRD_PATH)
    assert (spec.id, spec.version) == ("srd-5.2.1", "0.4.0")
    rules = DataRules(spec)
    assert set(rules.payload_models) == {
        "ability_check",
        "saving_throw",
        "attack",
        "damage",
        "heal",
        "apply_condition",
        "remove_condition",
        "advance_time",
        "give",
        "take",
    }
    schema = rules.payload_models["attack"].model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"target_id", "advantage", "disadvantage"}


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda d: d["actions"]["attack"].update(check="d100"), "actions.attack.check"),
        (lambda d: d["actions"]["attack"].update(modifier="mod[input.ability] + bogus"), "bogus"),
        (lambda d: d["actions"]["attack"].update(outcomes="missing"), "actions.attack.outcomes"),
        (lambda d: d["conditions"]["prone"].update(attacker="lucky"), "conditions.prone"),
        (lambda d: d["actions"]["attack"].update(modifier="1 / 2"), "actions.attack.modifier"),
        (lambda d: d.update(version="v4"), "version"),
        (lambda d: d["actions"].update(dance={"builtin": "tango"}), "actions.dance"),
        (lambda d: d["outcomes"]["attack"].append({"when": "die > 0", "outcome": "win"}), "win"),
        (lambda d: d["stats"].update({"Bad Key": {"type": "integer", "range": [1, 2]}}), "Bad Key"),
        (lambda d: d.update(unexpected=1), "unexpected"),
    ],
)
def test_validation_errors_name_the_offending_entry(tmp_path, mutate, message):
    data = spec_dict()
    mutate(data)
    with pytest.raises(RulebookError, match=message):
        load_rulebook(write_spec(tmp_path, data))


def test_check_command_reports_problems_in_plain_text(tmp_path, capsys):
    from saga_trpg.rulebooks.__main__ import main

    data = spec_dict()
    data["actions"]["attack"]["check"] = "d100"
    path = write_spec(tmp_path, data)
    assert main(["check", str(path)]) == 1
    assert "actions.attack.check" in capsys.readouterr().err
    assert main(["check", str(SRD_PATH)]) == 0
    assert "srd-5.2.1@0.4.0" in capsys.readouterr().out


# --- equivalence with the hand-written SRD module --------------------------------------------

SCENARIOS = [
    ("attack", {"target_id": "opponent"}, [("1d20", [20], 0), ("2d8+3", [4, 5], 3)]),
    ("attack", {"target_id": "opponent"}, [("1d20", [1], 0)]),
    ("attack", {"target_id": "opponent"}, [("1d20", [10], 0), ("1d8+3", [6], 3)]),
    (
        "attack",
        {"target_id": "opponent", "advantage": True},
        [("1d20", [3], 0), ("1d20", [17], 0), ("1d8+3", [2], 3)],
    ),
    (
        "attack",
        {"target_id": "opponent", "disadvantage": True},
        [("1d20", [3], 0), ("1d20", [17], 0)],
    ),
    (
        "attack",
        {"target_id": "opponent", "advantage": True, "disadvantage": True},
        [("1d20", [12], 0), ("1d8+3", [1], 3)],
    ),
    ("ability_check", {"ability": "strength", "difficulty": 15}, [("1d20", [11], 0)]),
    (
        "ability_check",
        {"ability": "strength", "difficulty": 15, "proficiency": "athletics"},
        [("1d20", [9], 0)],
    ),
    (
        "ability_check",
        {"ability": "wisdom", "difficulty": 10, "proficiency": "stealth"},
        [("1d20", [9], 0)],
    ),
    ("ability_check", {"ability": "strength", "difficulty": 40}, [("1d20", [20], 0)]),
    ("saving_throw", {"ability": "strength", "difficulty": 15}, [("1d20", [10], 0)]),
    (
        "saving_throw",
        {"ability": "dexterity", "difficulty": 15, "advantage": True},
        [("1d20", [2], 0), ("1d20", [14], 0)],
    ),
    ("damage", {"target_id": "opponent", "amount": 7}, []),
    ("damage", {"target_id": "opponent", "amount": 100}, []),
    ("heal", {"target_id": "player", "amount": 3}, []),
    ("apply_condition", {"target_id": "opponent", "condition": "prone"}, []),
    ("advance_time", {"amount": 2, "unit": "round"}, []),
    ("give", {"target_id": "player", "item": "torch", "amount": 3}, []),
]


@pytest.mark.parametrize("kind,payload,rolls", SCENARIOS)
def test_data_engine_matches_python_srd_030_mechanics(kind, payload, rolls):
    results = []
    for rules in (SRD521Rules("0.3.0"), DataRules(load_rulebook(SRD_PATH))):
        dice = ScriptedDice(*rolls)
        engine = engine_for(rules, dice)
        if kind == "attack" and rolls[0][1] == [20]:
            engine.state.actors["opponent"].stats["armor_class"] = 99
        results.append(engine.dispatch(event(kind, payload)).entry.result)
        assert dice.remaining == []
    python, data = results
    assert data.state == python.state
    assert data.rolls == python.rolls
    assert data.patches == python.patches
    assert data.resolution == python.resolution


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("attack", {"target_id": "opponent", "attack_bonus": 999}),
        ("attack", {"target_id": "missing"}),
        ("ability_check", {"ability": "strength", "difficulty": "15"}),
        ("ability_check", {"ability": "luck", "difficulty": 15}),
        ("overcome", {"skill": "investigate", "difficulty": 2}),
        ("damage", {"target_id": "opponent", "amount": 0}),
        ("take", {"target_id": "player", "item": "torch", "amount": 1}),
        ("remove_condition", {"target_id": "player", "condition": "prone"}),
        ("apply_condition", {"target_id": "player", "condition": "exhaustion"}),
        ("advance_time", {"amount": 1, "unit": "week"}),
        ("give", {"target_id": "player", "item": "Silver Key", "amount": 1}),
    ],
)
def test_data_engine_rejects_the_same_inputs_before_dice(kind, payload):
    dice = ScriptedDice()
    engine = engine_for(DataRules(load_rulebook(SRD_PATH)), dice)
    before = engine.state.model_copy(deep=True)
    with pytest.raises(ValueError):
        engine.dispatch(event(kind, payload))
    assert dice.calls == [] and engine.state == before and engine.journal.entries() == []


@pytest.mark.parametrize(
    "stat,value", [("weapon_damage", "broken"), ("weapon_ability", "luck"), ("strength", 0)]
)
def test_data_engine_validates_actor_stats_against_the_rulebook(stat, value):
    dice = ScriptedDice()
    engine = engine_for(DataRules(load_rulebook(SRD_PATH)), dice)
    engine.state.actors["player"].stats[stat] = value
    with pytest.raises(ValueError, match=stat):
        engine.dispatch(event("attack", {"target_id": "opponent"}))
    assert dice.calls == []


def test_data_engine_replays_from_recorded_results():
    dice = ScriptedDice(("1d20", [10], 0), ("1d8+3", [6], 3))
    engine = engine_for(DataRules(load_rulebook(SRD_PATH)), dice)
    initial = engine.state.model_copy(deep=True)
    engine.dispatch(event("attack", {"target_id": "opponent"}))
    engine.dispatch(event("give", {"target_id": "player", "item": "torch", "amount": 1}, "g"))
    assert replay_recorded_results(initial, engine.journal.entries()) == engine.state
    assert engine.dispatch(event("attack", {"target_id": "opponent"})).replayed
    assert len(dice.calls) == 2


# --- condition effects come from the table, not from code ------------------------------------


def srd_engine(dice, *, attacker=(), target=()):
    engine = engine_for(DataRules(load_rulebook(SRD_PATH)), dice)
    engine.state.actors["player"].conditions.update(attacker)
    engine.state.actors["opponent"].conditions.update(target)
    return engine


def test_prone_attacker_rolls_with_disadvantage_from_the_table():
    dice = ScriptedDice(("1d20", [15], 0), ("1d20", [4], 0))
    engine = srd_engine(dice, attacker=["prone"])
    result = engine.dispatch(event("attack", {"target_id": "opponent"})).entry.result
    assert len(result.rolls) == 2 and result.resolution.outcome == "miss"
    assert result.resolution.total == 4 + 5
    assert any("disadvantage" in line and "prone" in line for line in result.trace)


def test_paralyzed_target_grants_advantage_and_cancels_against_disadvantage():
    dice = ScriptedDice(("1d20", [3], 0), ("1d20", [16], 0), ("1d8+3", [2], 3))
    engine = srd_engine(dice, target=["paralyzed"])
    result = engine.dispatch(event("attack", {"target_id": "opponent"})).entry.result
    assert result.resolution.total == 16 + 5 and result.resolution.outcome == "hit"
    dice = ScriptedDice(("1d20", [9], 0), ("1d8+3", [1], 3))
    engine = srd_engine(dice, attacker=["poisoned"], target=["paralyzed"])
    result = engine.dispatch(event("attack", {"target_id": "opponent"})).entry.result
    d20s = [roll for roll in result.rolls if roll.roll_key.startswith("attack:")]
    assert len(d20s) == 1 and result.resolution.total == 14
    assert [line for line in result.trace if "poisoned" in line or "paralyzed" in line]


def test_poisoned_actor_has_disadvantage_on_checks_but_not_on_state_actions():
    dice = ScriptedDice(("1d20", [18], 0), ("1d20", [2], 0))
    engine = srd_engine(dice, attacker=["poisoned"])
    result = engine.dispatch(
        event("ability_check", {"ability": "strength", "difficulty": 10})
    ).entry.result
    assert result.resolution.total == 2 + 3
    engine.dispatch(event("heal", {"target_id": "player", "amount": 1}, "h"))
    assert len(dice.calls) == 2


def test_incapacitating_conditions_block_rolls_but_not_state_changes():
    dice = ScriptedDice()
    engine = srd_engine(dice, attacker=["unconscious"])
    with pytest.raises(ValueError, match="unconscious"):
        engine.dispatch(event("attack", {"target_id": "opponent"}))
    with pytest.raises(ValueError, match="unconscious"):
        engine.dispatch(event("saving_throw", {"ability": "strength", "difficulty": 10}))
    engine.dispatch(event("remove_condition", {"target_id": "player", "condition": "unconscious"}))
    assert dice.calls == [] and engine.state.actors["player"].conditions == set()


# --- a tiny rulebook a creator could write -------------------------------------------------

TINY = {
    "schema_version": 1,
    "id": "tiny",
    "version": "0.1.0",
    "title": "Tiny",
    "license": {"id": "CC0-1.0", "attribution": "none"},
    "stats": {"might": {"type": "integer", "range": [1, 10]}},
    "checks": {"d6": {"roll": "1d6", "advantage": "none"}},
    "outcomes": {
        "basic": [
            {"when": "total >= target", "outcome": "success"},
            {"when": "total < target", "outcome": "failure"},
        ]
    },
    "actions": {
        "try": {
            "role": "check",
            "input": {"difficulty": {"type": "integer", "range": [1, 20]}},
            "check": "d6",
            "modifier": "actor.might",
            "target": "input.difficulty",
            "outcomes": "basic",
        },
        "hurt": {"builtin": "hp_change", "sign": -1},
    },
    "conditions": {"dazed": {"cannot_act": True}},
    "example_world": {"actors": {"player": {"hp": 5, "max_hp": 5, "stats": {"might": 3}}}},
}


def test_tiny_rulebook_runs_with_its_own_example_world(tmp_path):
    spec = load_rulebook(write_spec(tmp_path, TINY))
    rules = DataRules(spec)
    dice = ScriptedDice(("1d6", [4], 0))
    engine = engine_for(rules, dice, state=rules.example_world("tiny-1"))
    result = engine.dispatch(event("try", {"difficulty": 7})).entry.result
    assert result.resolution.outcome == "success" and result.resolution.total == 7
    engine.dispatch(event("hurt", {"target_id": "player", "amount": 2}, "h"))
    assert engine.state.actors["player"].hp == 3
    with pytest.raises(ValueError):
        engine.dispatch(event("try", {"difficulty": 7, "advantage": True}, "x"))


# --- registry -------------------------------------------------------------------------------


def test_registry_serves_srd_040_from_the_rulebook_and_keeps_python_versions():
    from saga_trpg.modules.catalog import SRD_LATEST, RulesetRegistry

    registry = RulesetRegistry()
    assert SRD_LATEST == "0.4.0"
    latest = registry.get("srd-5.2.1", SRD_LATEST)
    assert isinstance(latest.rules, DataRules)
    assert latest.manifest.ruleset_version == "0.4.0"
    assert "condition effects on attack, check and save rolls" in " ".join(
        latest.manifest.implemented
    )
    assert set(latest.manifest.event_payload_schemas) == set(latest.rules.payload_models)
    assert latest.example_world("w").actors["player"].stats["strength"] == 16
    assert latest.example_world("w") == registry.get("srd-5.2.1", "0.3.0").example_world("w")
    for version in ("0.1.0", "0.2.0", "0.3.0"):
        assert isinstance(registry.get("srd-5.2.1", version).rules, SRD521Rules)
    assert sorted(
        m.ruleset_version for m in registry.manifests() if m.ruleset_id == "srd-5.2.1"
    ) == ["0.1.0", "0.2.0", "0.3.0", "0.4.0"]
