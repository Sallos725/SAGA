from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from saga_trpg.runtime.engine import RuntimeEngine
from saga_trpg.runtime.journal import InMemoryJournal
from saga_trpg.runtime.replay import replay_recorded_results
from saga_trpg.runtime.rng import DeterministicDice


def make_engine() -> RuntimeEngine:
    return RuntimeEngine(
        initial_state=WorldState(
            world_id="test",
            actors={
                "player": ActorState(actor_id="player", hp=20, max_hp=20),
                "goblin": ActorState(actor_id="goblin", hp=12, max_hp=12),
            },
        ),
        rules=GenericFantasyRules(),
        dice=DeterministicDice("fixed-seed"),
        journal=InMemoryJournal(),
    )


def test_same_committed_event_is_idempotent() -> None:
    engine = make_engine()
    event = GameEvent(
        event_id="turn-1-action-1",
        kind="attack",
        actor_id="player",
        payload={"target_id": "goblin", "attack_bonus": 20, "target_ac": 10, "damage": "1d6+2"},
    )
    first = engine.dispatch(event)
    hp_after_first = engine.state.actors["goblin"].hp
    second = engine.dispatch(event)

    assert first.replayed is False
    assert second.replayed is True
    assert engine.state.actors["goblin"].hp == hp_after_first
    assert first.entry.result.rolls == second.entry.result.rolls
    assert len(engine.journal.entries()) == 1


def test_replay_uses_recorded_results() -> None:
    engine = make_engine()
    initial = engine.state.model_copy(deep=True)
    engine.dispatch(
        GameEvent(
            kind="attack",
            actor_id="player",
            payload={"target_id": "goblin", "attack_bonus": 20, "target_ac": 10, "damage": "1d6+2"},
        )
    )
    rebuilt = replay_recorded_results(initial, engine.journal.entries())
    assert rebuilt == engine.state


def test_conditions_serialize_in_sorted_order_regardless_of_insertion() -> None:
    names = ["prone", "poisoned", "blinded", "stunned", "charmed"]
    dumps = set()
    for start in range(len(names)):
        rotated = names[start:] + names[:start]
        actor = ActorState(actor_id="a", conditions=set())
        for name in rotated:
            actor.conditions.add(name)
        dumps.add(actor.model_dump_json())
        assert actor.model_dump(mode="json")["conditions"] == sorted(names)
    assert len(dumps) == 1
    assert ActorState.model_validate_json(dumps.pop()).conditions == set(names)
