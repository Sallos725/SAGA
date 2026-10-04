from __future__ import annotations

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, RuleResult, StatePatch, WorldState
from saga_trpg.ports.dice import DicePort


class GenericFantasyRules:
    name = "generic-fantasy"
    version = "0.1.0"

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult:
        next_state = state.model_copy(deep=True)
        if event.kind == "attack":
            return self._attack(next_state, event, dice)
        if event.kind == "set_flag":
            return self._set_flag(next_state, event)
        if event.kind == "time_advance":
            return self._time_advance(next_state, event)
        return RuleResult(state=next_state, trace=[f"no-op: unhandled event kind {event.kind}"])

    def _ensure_actor(self, state: WorldState, actor_id: str) -> ActorState:
        if actor_id not in state.actors:
            state.actors[actor_id] = ActorState(actor_id=actor_id)
        return state.actors[actor_id]

    def _attack(self, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult:
        if not event.event_id:
            raise ValueError("event_id must be assigned before rule dispatch")
        target_id = str(event.payload["target_id"])
        attack_bonus = int(event.payload.get("attack_bonus", 0))
        target_ac = int(event.payload.get("target_ac", 10))
        damage_expr = str(event.payload.get("damage", "1d4"))

        self._ensure_actor(state, event.actor_id)
        target = self._ensure_actor(state, target_id)

        attack = dice.roll(
            world_id=state.world_id,
            event_id=event.event_id,
            roll_key="attack",
            expression=f"1d20{attack_bonus:+d}" if attack_bonus else "1d20",
        )
        rolls = [attack]
        facts: list[str] = []
        patches: list[StatePatch] = []
        hit = attack.total >= target_ac

        if not hit:
            facts.append(f"{event.actor_id} misses {target_id} ({attack.total} vs AC {target_ac}).")
            return RuleResult(
                state=state,
                facts=facts,
                rolls=rolls,
                trace=["generic-fantasy.attack: miss"],
            )

        damage = dice.roll(
            world_id=state.world_id,
            event_id=event.event_id,
            roll_key="damage",
            expression=damage_expr,
        )
        rolls.append(damage)
        before = target.hp
        target.hp = max(0, min(target.max_hp, target.hp - max(0, damage.total)))
        patches.append(StatePatch(path=f"actors.{target_id}.hp", before=before, after=target.hp))
        facts.append(
            f"{event.actor_id} hits {target_id} ({attack.total} vs AC {target_ac}) "
            f"for {damage.total} damage; HP {before}->{target.hp}."
        )
        return RuleResult(
            state=state,
            facts=facts,
            rolls=rolls,
            patches=patches,
            trace=["generic-fantasy.attack: hit"],
        )

    def _set_flag(self, state: WorldState, event: GameEvent) -> RuleResult:
        key = str(event.payload["key"])
        value = event.payload.get("value")
        before = state.flags.get(key)
        state.flags[key] = value
        return RuleResult(
            state=state,
            facts=[f"World flag {key} changed."],
            patches=[StatePatch(path=f"flags.{key}", before=before, after=value)],
            trace=["generic-fantasy.set_flag"],
        )

    def _time_advance(self, state: WorldState, event: GameEvent) -> RuleResult:
        amount = int(event.payload.get("ticks", 1))
        if amount < 0:
            raise ValueError("time cannot move backwards through time_advance")
        before = state.simulated_time
        state.simulated_time += amount
        return RuleResult(
            state=state,
            facts=[f"Simulated time advanced by {amount}."],
            patches=[StatePatch(path="simulated_time", before=before, after=state.simulated_time)],
            trace=["generic-fantasy.time_advance"],
        )
