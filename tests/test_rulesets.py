import pytest

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import RollRecord
from saga_trpg.modules.catalog import RulesetRegistry
from saga_trpg.runtime.engine import RuntimeEngine
from saga_trpg.runtime.journal import InMemoryJournal
from saga_trpg.runtime.replay import replay_recorded_results


class ScriptedDice:
    def __init__(self, *rolls):
        self.remaining = list(rolls)
        self.calls = []

    def roll(self, *, world_id, event_id, roll_key, expression):
        self.calls.append((roll_key, expression))
        expected_expression, raw, modifier = self.remaining.pop(0)
        assert expression == expected_expression
        return RollRecord(
            roll_key=roll_key,
            expression=expression,
            raw=raw,
            modifier=modifier,
            total=sum(raw) + modifier,
        )


def engine_for(ruleset_id, dice):
    definition = RulesetRegistry().get(ruleset_id, "0.1.0")
    return RuntimeEngine(
        initial_state=definition.example_world("sample"),
        rules=definition.rules,
        dice=dice,
        journal=InMemoryJournal(),
    )


def event(kind, payload, event_id="action-1"):
    return GameEvent(event_id=event_id, kind=kind, actor_id="player", payload=payload)


def test_srd_critical_doubles_damage_dice_not_modifier_and_retries():
    dice = ScriptedDice(("1d20", [20], 0), ("2d8+3", [4, 5], 3))
    engine = engine_for("srd-5.2.1", dice)
    initial = engine.state.model_copy(deep=True)
    engine.state.actors["opponent"].stats["armor_class"] = 99
    initial.actors["opponent"].stats["armor_class"] = 99
    action = event("attack", {"target_id": "opponent"})
    first = engine.dispatch(action)
    assert first.entry.result.resolution.outcome == "critical_hit"
    assert first.entry.result.resolution.damage == 12
    assert engine.state.actors["opponent"].hp == initial.actors["opponent"].hp - 12
    assert engine.dispatch(action).replayed
    assert len(dice.calls) == 2
    assert replay_recorded_results(initial, engine.journal.entries()) == engine.state


def test_srd_natural_one_misses_without_damage_roll():
    dice = ScriptedDice(("1d20", [1], 0))
    engine = engine_for("srd-5.2.1", dice)
    engine.state.actors["opponent"].stats["armor_class"] = 1
    before_hp = engine.state.actors["opponent"].hp
    result = engine.dispatch(event("attack", {"target_id": "opponent"})).entry.result
    assert result.resolution.outcome == "miss"
    assert engine.state.actors["opponent"].hp == before_hp
    assert len(dice.calls) == 1


@pytest.mark.parametrize(
    "advantage,disadvantage,raw,selected",
    [
        (True, False, [3, 17], 17),
        (False, True, [3, 17], 3),
        (True, True, [10], 10),
        (False, False, [10], 10),
    ],
)
def test_srd_advantage_and_disadvantage(advantage, disadvantage, raw, selected):
    dice = ScriptedDice(*[("1d20", [value], 0) for value in raw])
    engine = engine_for("srd-5.2.1", dice)
    result = engine.dispatch(
        event(
            "ability_check",
            {
                "ability": "strength",
                "difficulty": 15,
                "advantage": advantage,
                "disadvantage": disadvantage,
            },
        )
    ).entry.result
    assert result.resolution.total == selected + 3
    assert len(result.rolls) == len(raw)


def test_srd_checks_have_no_automatic_twenty_success_and_use_stored_proficiency():
    dice = ScriptedDice(("1d20", [20], 0), ("1d20", [10], 0))
    engine = engine_for("srd-5.2.1", dice)
    check = engine.dispatch(event("ability_check", {"ability": "strength", "difficulty": 40}))
    assert check.entry.result.resolution.outcome == "failure"
    save = engine.dispatch(event("saving_throw", {"ability": "strength", "difficulty": 15}, "save"))
    assert save.entry.result.resolution.total == 15
    assert save.entry.result.resolution.outcome == "success"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([1, 1, 1, 1], "failure"),
        ([1, 1, 3, 3], "tie"),
        ([1, 2, 3, 3], "success"),
        ([2, 3, 3, 3], "success_with_style"),
    ],
)
def test_fate_four_outcomes_preserve_raw_dice(raw, expected):
    dice = ScriptedDice(("4d3-8", raw, -8))
    engine = engine_for("fate-condensed", dice)
    result = engine.dispatch(
        event("overcome", {"skill": "investigate", "difficulty": 2})
    ).entry.result
    assert result.resolution.outcome == expected
    assert result.rolls[0].raw == raw
    assert result.resolution.total == sum(raw) - 8 + 2
    assert result.resolution.pending_adjudication == (expected in {"failure", "tie"})
    if expected == "success_with_style":
        assert result.state.flags["fate_boosts"]["action-1"]["owner_id"] == "player"


@pytest.mark.parametrize("raw,invokes", [([1, 2, 3, 3], 1), ([3, 3, 3, 3], 2)])
def test_fate_creates_aspect_with_recorded_free_invokes(raw, invokes):
    dice = ScriptedDice(("4d3-8", raw, -8))
    engine = engine_for("fate-condensed", dice)
    action = event(
        "create_advantage",
        {
            "skill": "investigate",
            "difficulty": 2,
            "aspect_name": "Mapped escape route",
        },
    )
    engine.dispatch(action)
    before = engine.state.model_copy(deep=True)
    aspect = engine.state.flags["fate_aspects"]["action-1"]
    assert aspect == {"name": "Mapped escape route", "owner_id": "player", "free_invokes": invokes}
    assert engine.dispatch(action).replayed
    assert engine.state == before
    assert len(dice.calls) == 1


@pytest.mark.parametrize(
    "ruleset_id,kind,payload",
    [
        ("srd-5.2.1", "attack", {"target_id": "opponent", "attack_bonus": 999}),
        ("srd-5.2.1", "attack", {"target_id": "missing"}),
        ("srd-5.2.1", "ability_check", {"ability": "strength", "difficulty": "15"}),
        ("srd-5.2.1", "overcome", {"skill": "investigate", "difficulty": 2}),
        ("fate-condensed", "attack", {"target_id": "opponent"}),
        ("fate-condensed", "overcome", {"skill": "missing", "difficulty": 2}),
        ("fate-condensed", "overcome", {"skill": "investigate", "difficulty": 2, "bonus": 999}),
    ],
)
def test_invalid_or_unsupported_actions_fail_before_tools(ruleset_id, kind, payload):
    dice = ScriptedDice()
    engine = engine_for(ruleset_id, dice)
    before = engine.state.model_copy(deep=True)
    with pytest.raises(ValueError):
        engine.dispatch(event(kind, payload))
    assert dice.calls == []
    assert engine.state == before
    assert engine.journal.entries() == []


def test_invalid_weapon_fails_even_when_attack_would_miss():
    dice = ScriptedDice()
    engine = engine_for("srd-5.2.1", dice)
    engine.state.actors["player"].stats["weapon_damage"] = "broken"
    with pytest.raises(ValueError):
        engine.dispatch(event("attack", {"target_id": "opponent"}))
    assert dice.calls == []


@pytest.mark.parametrize(
    "raw,outcome,pending",
    [
        ([1, 1, 1, 1], "failure", True),
        ([1, 1, 3, 3], "tie", False),
    ],
)
def test_fate_advantage_failure_and_tie_do_not_grant_aspect_invokes(raw, outcome, pending):
    engine = engine_for("fate-condensed", ScriptedDice(("4d3-8", raw, -8)))
    result = engine.dispatch(
        event(
            "create_advantage",
            {
                "skill": "investigate",
                "difficulty": 2,
                "aspect_name": "Mapped route",
            },
        )
    ).entry.result
    assert result.resolution.outcome == outcome
    assert result.resolution.pending_adjudication is pending
    assert result.state.flags["fate_aspects"] == {}
    assert bool(result.state.flags["fate_boosts"]) == (outcome == "tie")


def test_fate_existing_aspect_is_rejected_before_dice():
    dice = ScriptedDice(("4d3-8", [3, 3, 3, 3], -8))
    engine = engine_for("fate-condensed", dice)
    payload = {"skill": "investigate", "difficulty": 2, "aspect_name": "Mapped route"}
    engine.dispatch(event("create_advantage", payload))
    with pytest.raises(ValueError, match="new aspects only"):
        engine.dispatch(event("create_advantage", payload, "duplicate-name"))
    assert len(dice.calls) == 1
