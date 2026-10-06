"""Small SRD 5.2.1 rules adaptation. Attribution and scope: docs/RULESET_LICENSES.md."""

from __future__ import annotations

import re
from typing import Literal, get_args

from pydantic import Field

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import (
    ActorState,
    RollRecord,
    RuleResolution,
    RuleResult,
    StatePatch,
    WorldState,
)
from saga_trpg.modules.validation import ClosedPayload, actor_in, boolean_stat, integer_stat
from saga_trpg.ports.dice import DicePort

Ability = Literal["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
_WEAPON_DAMAGE = re.compile(r"([1-9][0-9]*)d(4|6|8|10|12)")


class RollCircumstances(ClosedPayload):
    advantage: bool = False
    disadvantage: bool = False


class AbilityCheck(RollCircumstances):
    ability: Ability
    difficulty: int = Field(ge=1, le=100)
    proficiency: str | None = Field(default=None, min_length=1, max_length=80)


class SavingThrow(RollCircumstances):
    ability: Ability
    difficulty: int = Field(ge=1, le=100)


class Attack(RollCircumstances):
    target_id: str = Field(min_length=1)


# Exhaustion has levels in the SRD and is left out until levels are tracked.
Condition = Literal[
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
]
TimeUnit = Literal["round", "minute", "hour", "day"]
_SECONDS = {"round": 6, "minute": 60, "hour": 3600, "day": 86400}


class HitPointChange(ClosedPayload):
    target_id: str = Field(min_length=1)
    amount: int = Field(ge=1, le=1000)


class Damage(HitPointChange):
    pass


class Heal(HitPointChange):
    pass


class ConditionChange(ClosedPayload):
    target_id: str = Field(min_length=1)
    condition: Condition


class ApplyCondition(ConditionChange):
    pass


class RemoveCondition(ConditionChange):
    pass


class AdvanceTime(ClosedPayload):
    amount: int = Field(ge=1, le=100_000)
    unit: TimeUnit


# Item keys are closed by shape only; what an item does is not modelled in this example.
class InventoryChange(ClosedPayload):
    target_id: str = Field(min_length=1)
    item: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    amount: int = Field(ge=1, le=1000)


class Give(InventoryChange):
    pass


class Take(InventoryChange):
    pass


ROLL_ACTIONS = {"ability_check": AbilityCheck, "saving_throw": SavingThrow, "attack": Attack}
STATE_ACTIONS = {
    "damage": Damage,
    "heal": Heal,
    "apply_condition": ApplyCondition,
    "remove_condition": RemoveCondition,
    "advance_time": AdvanceTime,
}
INVENTORY_ACTIONS = {"give": Give, "take": Take}
_ACTIONS_BY_VERSION = {
    "0.1.0": ROLL_ACTIONS,
    "0.2.0": {**ROLL_ACTIONS, **STATE_ACTIONS},
    "0.3.0": {**ROLL_ACTIONS, **STATE_ACTIONS, **INVENTORY_ACTIONS},
}
LATEST_VERSION = "0.3.0"


class SRD521Rules:
    name = "srd-5.2.1"

    def __init__(self, version: str = LATEST_VERSION) -> None:
        # Worlds pin a version; older versions keep their exact action set and semantics.
        if version not in _ACTIONS_BY_VERSION:
            raise ValueError(f"unsupported {self.name} version: {version}")
        self.version = version
        self.payload_models = dict(_ACTIONS_BY_VERSION[version])

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult:
        if not event.event_id:
            raise ValueError("event id must be assigned before dispatch")
        model = self.payload_models.get(event.kind)
        if model is None:
            raise ValueError(f"unsupported {self.name} action: {event.kind}")
        payload = model.model_validate(event.payload)
        actor = actor_in(state, event.actor_id)
        if isinstance(payload, Attack):
            return self._attack(state, actor, event, payload, dice)
        if isinstance(payload, HitPointChange):
            return self._hit_points(state, event, payload)
        if isinstance(payload, ConditionChange):
            return self._condition(state, event, payload)
        if isinstance(payload, AdvanceTime):
            return self._advance_time(state, event, payload)
        if isinstance(payload, InventoryChange):
            return self._inventory(state, event, payload)

        modifier = self._ability_modifier(actor, payload.ability)
        proficiency_key = (
            f"save_proficient:{payload.ability}"
            if isinstance(payload, SavingThrow)
            else f"proficient:{payload.proficiency}"
            if payload.proficiency
            else None
        )
        if proficiency_key and boolean_stat(actor, proficiency_key):
            modifier += integer_stat(actor, "proficiency_bonus", minimum=2, maximum=9)
        selected, rolls = self._d20(state, event, payload, dice)
        total = selected + modifier
        outcome = "success" if total >= payload.difficulty else "failure"
        return RuleResult(
            state=state.model_copy(deep=True),
            rolls=rolls,
            resolution=RuleResolution(outcome=outcome, total=total, target=payload.difficulty),
            facts=[
                f"{event.actor_id}: {event.kind} {outcome} ({total} vs DC {payload.difficulty})."
            ],
            trace=[
                f"{self.name}@{self.version}.{event.kind}",
                f"d20={selected}; modifier={modifier}",
            ],
        )

    def _ability_modifier(self, actor: ActorState, ability: str) -> int:
        return (integer_stat(actor, ability, minimum=1, maximum=30) - 10) // 2

    def _d20(
        self, state: WorldState, event: GameEvent, payload: RollCircumstances, dice: DicePort
    ) -> tuple[int, list[RollRecord]]:
        count = 2 if payload.advantage != payload.disadvantage else 1
        rolls = [
            dice.roll(
                world_id=state.world_id,
                event_id=event.event_id,
                roll_key=f"{event.kind}:{index}",
                expression="1d20",
            )
            for index in range(count)
        ]
        values = [roll.total for roll in rolls]
        return (max(values) if payload.advantage else min(values)), rolls

    def _attack(
        self,
        state: WorldState,
        actor: ActorState,
        event: GameEvent,
        payload: Attack,
        dice: DicePort,
    ) -> RuleResult:
        target = actor_in(state, payload.target_id)
        if actor.hp <= 0 or not 0 <= target.hp <= target.max_hp or target.max_hp <= 0:
            raise ValueError("attack requires a conscious attacker and valid target HP")
        ability = actor.stats.get("weapon_ability")
        if ability not in get_args(Ability):
            raise ValueError("weapon_ability must identify a supported ability")
        modifier = self._ability_modifier(actor, ability)
        attack_modifier = modifier
        if boolean_stat(actor, "weapon_proficient"):
            attack_modifier += integer_stat(actor, "proficiency_bonus", minimum=2, maximum=9)
        armor_class = integer_stat(target, "armor_class", minimum=1, maximum=100)
        expression = actor.stats.get("weapon_damage")
        match = _WEAPON_DAMAGE.fullmatch(expression) if isinstance(expression, str) else None
        if match is None or int(match[1]) > 50:
            raise ValueError("weapon_damage must be 1..50 d4/d6/d8/d10/d12 dice without modifiers")

        # Validate all attack and damage data before the first tool call, including on a miss.
        selected, rolls = self._d20(state, event, payload, dice)
        total = selected + attack_modifier
        hit = selected == 20 or (selected != 1 and total >= armor_class)
        outcome = "critical_hit" if selected == 20 else "hit" if hit else "miss"
        next_state = state.model_copy(deep=True)
        patches = []
        damage = 0
        if hit:
            count = int(match[1]) * (2 if selected == 20 else 1)
            damage_expression = f"{count}d{match[2]}{modifier:+d}"
            rolled = dice.roll(
                world_id=state.world_id,
                event_id=event.event_id,
                roll_key="damage",
                expression=damage_expression,
            )
            rolls.append(rolled)
            damage = max(0, rolled.total)
            remaining = max(0, target.hp - damage)
            next_state.actors[target.actor_id].hp = remaining
            patches.append(
                StatePatch(path=f"actors.{target.actor_id}.hp", before=target.hp, after=remaining)
            )
        return RuleResult(
            state=next_state,
            rolls=rolls,
            patches=patches,
            resolution=RuleResolution(
                outcome=outcome, total=total, target=armor_class, damage=damage
            ),
            facts=[
                f"{event.actor_id}: {outcome} against {target.actor_id}; "
                f"damage {damage}; HP {next_state.actors[target.actor_id].hp}."
            ],
            trace=[
                f"{self.name}@{self.version}.attack",
                f"d20={selected}; modifier={attack_modifier}",
            ],
        )

    def _hit_points(
        self, state: WorldState, event: GameEvent, payload: HitPointChange
    ) -> RuleResult:
        target = actor_in(state, payload.target_id)
        if not 0 <= target.hp <= target.max_hp or target.max_hp <= 0:
            raise ValueError(f"{target.actor_id} has invalid HP")
        if isinstance(payload, Damage):
            after = max(0, target.hp - payload.amount)
        else:
            after = min(target.max_hp, target.hp + payload.amount)
        next_state = state.model_copy(deep=True)
        next_state.actors[target.actor_id].hp = after
        return RuleResult(
            state=next_state,
            patches=[
                StatePatch(path=f"actors.{target.actor_id}.hp", before=target.hp, after=after)
            ],
            facts=[
                f"{event.actor_id}: {event.kind} {payload.amount} to {target.actor_id}; "
                f"HP {target.hp}->{after}/{target.max_hp}."
            ],
            trace=[f"{self.name}@{self.version}.{event.kind}", f"amount={payload.amount}"],
        )

    def _condition(
        self, state: WorldState, event: GameEvent, payload: ConditionChange
    ) -> RuleResult:
        target = actor_in(state, payload.target_id)
        present = payload.condition in target.conditions
        if isinstance(payload, ApplyCondition) and present:
            raise ValueError(f"{target.actor_id} already has condition {payload.condition}")
        if isinstance(payload, RemoveCondition) and not present:
            raise ValueError(f"{target.actor_id} does not have condition {payload.condition}")
        next_state = state.model_copy(deep=True)
        conditions = next_state.actors[target.actor_id].conditions
        if isinstance(payload, ApplyCondition):
            conditions.add(payload.condition)
        else:
            conditions.remove(payload.condition)
        return RuleResult(
            state=next_state,
            patches=[
                StatePatch(
                    path=f"actors.{target.actor_id}.conditions",
                    before=sorted(target.conditions),
                    after=sorted(conditions),
                )
            ],
            facts=[
                f"{event.actor_id}: {event.kind} {payload.condition} on {target.actor_id}; "
                f"conditions {sorted(conditions)}."
            ],
            trace=[f"{self.name}@{self.version}.{event.kind}", f"condition={payload.condition}"],
        )

    def _advance_time(
        self, state: WorldState, event: GameEvent, payload: AdvanceTime
    ) -> RuleResult:
        seconds = payload.amount * _SECONDS[payload.unit]
        next_state = state.model_copy(deep=True)
        next_state.simulated_time = state.simulated_time + seconds
        return RuleResult(
            state=next_state,
            patches=[
                StatePatch(
                    path="simulated_time",
                    before=state.simulated_time,
                    after=next_state.simulated_time,
                )
            ],
            facts=[
                f"{event.actor_id}: advance_time {payload.amount} {payload.unit}(s); "
                f"simulated time {next_state.simulated_time}s."
            ],
            trace=[f"{self.name}@{self.version}.advance_time", f"seconds={seconds}"],
        )

    def _inventory(
        self, state: WorldState, event: GameEvent, payload: InventoryChange
    ) -> RuleResult:
        target = actor_in(state, payload.target_id)
        before = target.inventory.get(payload.item, 0)
        if isinstance(payload, Take) and before < payload.amount:
            raise ValueError(
                f"{target.actor_id} holds {before} {payload.item}, cannot take {payload.amount}"
            )
        after = before + payload.amount if isinstance(payload, Give) else before - payload.amount
        next_state = state.model_copy(deep=True)
        inventory = next_state.actors[target.actor_id].inventory
        if after > 0:
            inventory[payload.item] = after
        else:
            del inventory[payload.item]
        return RuleResult(
            state=next_state,
            patches=[
                StatePatch(
                    path=f"actors.{target.actor_id}.inventory.{payload.item}",
                    before=before,
                    after=after,
                )
            ],
            facts=[
                f"{event.actor_id}: {event.kind} {payload.amount} {payload.item} "
                f"{'to' if isinstance(payload, Give) else 'from'} {target.actor_id}; "
                f"now {after}."
            ],
            trace=[f"{self.name}@{self.version}.{event.kind}", f"item={payload.item}"],
        )


def example_world(world_id: str) -> WorldState:
    return WorldState(
        world_id=world_id,
        actors={
            "player": ActorState(
                actor_id="player",
                hp=20,
                max_hp=20,
                stats={
                    "strength": 16,
                    "dexterity": 12,
                    "constitution": 14,
                    "intelligence": 10,
                    "wisdom": 12,
                    "charisma": 10,
                    "proficiency_bonus": 2,
                    "save_proficient:strength": True,
                    "proficient:athletics": True,
                    "weapon_ability": "strength",
                    "weapon_damage": "1d8",
                    "weapon_proficient": True,
                    "armor_class": 15,
                },
            ),
            "opponent": ActorState(
                actor_id="opponent", hp=30, max_hp=30, stats={"armor_class": 13}
            ),
        },
    )
