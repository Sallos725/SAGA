from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import ActorState, WorldState
from nmos_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from nmos_trpg.runtime.engine import RuntimeEngine
from nmos_trpg.runtime.journal import InMemoryJournal
from nmos_trpg.runtime.replay import replay_recorded_results
from nmos_trpg.runtime.rng import DeterministicDice


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
