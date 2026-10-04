"""Tiny benchmark RuleModule, executed by normal SAGA dispatch/replay/tape paths."""

from pydantic import Field

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import RollRecord, RuleResult, StatePatch, WorldState
from saga_trpg.modules.validation import ClosedPayload
from saga_trpg.ports.dice import DicePort


class EmptyPayload(ClosedPayload):
    pass


class UnlockPayload(ClosedPayload):
    inscription: str = Field(max_length=200)
    warning: str = Field(max_length=200)
    item: str = Field(max_length=80)


class RuinRules:
    name = "experiment.sealed-ruin"
    version = "1"

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult:
        actor = state.actors[event.actor_id]
        before = dict(state.flags)
        rolls = []
        if event.kind == "unlock":
            payload = UnlockPayload.model_validate(event.payload)
            if payload.item != "silver_key" or actor.inventory.get(payload.item, 0) < 1:
                raise ValueError("required resource unavailable")
            if (
                payload.inscription != state.actors["a"].stats["inscription"]
                or payload.warning != state.actors["c"].stats["warning"]
            ):
                raise ValueError("the supplied ritual does not open the gate")
            state.flags["gate_open"] = True
        elif event.kind in ("wait", "brace", "force"):
            EmptyPayload.model_validate(event.payload)
            if event.kind == "brace":
                if actor.inventory.get("lever", 0) < 1 or state.flags["braced"]:
                    raise ValueError("bracing prerequisite not met")
                state.flags["braced"] = True
            elif event.kind == "force":
                if actor.inventory.get("hammer", 0) < 1:
                    raise ValueError("required resource unavailable")
                roll = dice.roll(
                    world_id=state.world_id,
                    event_id=event.event_id or "",
                    roll_key="effort",
                    expression="1d4",
                )
                rolls.append(roll)
                damage = roll.total + (4 if state.flags["braced"] else 0)
                state.flags["gate_integrity"] = max(0, state.flags["gate_integrity"] - damage)
                state.flags["gate_open"] = state.flags["gate_integrity"] == 0
        else:
            raise ValueError("unsupported ruin action")
        return RuleResult(
            state=state,
            rolls=rolls,
            facts=[f"{event.actor_id}: {event.kind} resolved"],
            patches=[
                StatePatch(path=f"flags.{key}", before=before[key], after=value)
                for key, value in state.flags.items()
                if before[key] != value
            ],
            trace=[f"sealed-ruin-v1: {event.kind}"],
        )


class FixedEffortDice:
    """Pure pre-recorded condition; action ordering/ids cannot change comparative luck."""

    def roll(self, *, world_id: str, event_id: str, roll_key: str, expression: str) -> RollRecord:
        if (roll_key, expression) != ("effort", "1d4"):
            raise ValueError("roll outside the declared fixed stochastic policy")
        return RollRecord(roll_key=roll_key, expression=expression, raw=[2], total=2)
