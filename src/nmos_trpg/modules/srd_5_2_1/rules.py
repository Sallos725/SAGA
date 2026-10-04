"""Small SRD 5.2.1 rules adaptation. Attribution and scope: docs/RULESET_LICENSES.md."""

from __future__ import annotations

import re
from typing import Literal, get_args

from pydantic import Field

from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import (
    ActorState,
    RollRecord,
    RuleResolution,
    RuleResult,
    StatePatch,
    WorldState,
)
from nmos_trpg.modules.validation import ClosedPayload, actor_in, boolean_stat, integer_stat
from nmos_trpg.ports.dice import DicePort

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


class SRD521Rules:
    name = "srd-5.2.1"
    version = "0.1.0"
    payload_models = {"ability_check": AbilityCheck, "saving_throw": SavingThrow, "attack": Attack}

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
