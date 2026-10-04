from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import ActorState, WorldState
from nmos_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from nmos_trpg.runtime.engine import RuntimeEngine
from nmos_trpg.runtime.journal import InMemoryJournal
from nmos_trpg.runtime.rng import DeterministicDice

engine = RuntimeEngine(
    initial_state=WorldState(
        world_id="demo",
        actors={
            "player": ActorState(actor_id="player", hp=20, max_hp=20),
            "goblin": ActorState(actor_id="goblin", hp=12, max_hp=12),
        },
    ),
    rules=GenericFantasyRules(),
    dice=DeterministicDice("demo-seed"),
    journal=InMemoryJournal(),
)

outcome = engine.dispatch(
    GameEvent(
        kind="attack",
        actor_id="player",
        payload={"target_id": "goblin", "attack_bonus": 5, "target_ac": 14, "damage": "1d8+3"},
    )
)
print(outcome.entry.model_dump_json(indent=2))
