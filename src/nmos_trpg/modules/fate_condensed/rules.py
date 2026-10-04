"""Small Fate Condensed adaptation. Attribution and scope: docs/RULESET_LICENSES.md."""

from __future__ import annotations

from pydantic import Field

from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import ActorState, RuleResolution, RuleResult, StatePatch, WorldState
from nmos_trpg.modules.validation import ClosedPayload, actor_in, integer_stat
from nmos_trpg.ports.dice import DicePort


class Overcome(ClosedPayload):
    skill: str = Field(min_length=1, max_length=80)
    difficulty: int = Field(ge=-10, le=20)
    boost_name: str = Field(default="Temporary advantage", min_length=1, max_length=200)


class CreateAdvantage(ClosedPayload):
    skill: str = Field(min_length=1, max_length=80)
    difficulty: int = Field(ge=-10, le=20)
    aspect_name: str = Field(min_length=1, max_length=200)


class Aspect(ClosedPayload):
    name: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    free_invokes: int = Field(ge=0)


class Boost(ClosedPayload):
    name: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)


class FateCondensedRules:
    name = "fate-condensed"
    version = "0.1.0"
    payload_models = {"overcome": Overcome, "create_advantage": CreateAdvantage}

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult:
        if not event.event_id:
            raise ValueError("event id must be assigned before dispatch")
        model = self.payload_models.get(event.kind)
        if model is None:
            raise ValueError(f"unsupported {self.name} action: {event.kind}")
        payload = model.model_validate(event.payload)
        actor = actor_in(state, event.actor_id)
        skill = integer_stat(actor, f"skill:{payload.skill}", minimum=-10, maximum=20)
        for key, record_type in (("fate_aspects", Aspect), ("fate_boosts", Boost)):
            records = state.flags.get(key, {})
            if not isinstance(records, dict):
                raise ValueError(f"{key} must contain structured records")
            for record in records.values():
                record_type.model_validate(record)
        if isinstance(payload, CreateAdvantage) and any(
            item["name"] == payload.aspect_name
            for item in state.flags.get("fate_aspects", {}).values()
        ):
            raise ValueError("this example supports creating new aspects only")

        # 1/2/3 map to -1/0/+1, preserving the exact four physical outcomes in the journal.
        roll = dice.roll(
            world_id=state.world_id,
            event_id=event.event_id,
            roll_key=event.kind,
            expression="4d3-8",
        )
        total = roll.total + skill
        shifts = total - payload.difficulty
        outcome = (
            "failure"
            if shifts < 0
            else "tie"
            if shifts == 0
            else "success"
            if shifts < 3
            else "success_with_style"
        )
        next_state = state.model_copy(deep=True)
        patches = []
        pending = shifts < 0 or (isinstance(payload, Overcome) and shifts == 0)
        if isinstance(payload, CreateAdvantage) and shifts > 0:
            aspect = Aspect(
                name=payload.aspect_name,
                owner_id=actor.actor_id,
                free_invokes=2 if shifts >= 3 else 1,
            ).model_dump()
            next_state.flags.setdefault("fate_aspects", {})[event.event_id] = aspect
            patches.append(StatePatch(path=f"flags.fate_aspects.{event.event_id}", after=aspect))
        elif (
            isinstance(payload, Overcome)
            and shifts >= 3
            or isinstance(payload, CreateAdvantage)
            and shifts == 0
        ):
            boost = Boost(
                name=payload.boost_name if isinstance(payload, Overcome) else payload.aspect_name,
                owner_id=actor.actor_id,
            ).model_dump()
            next_state.flags.setdefault("fate_boosts", {})[event.event_id] = boost
            patches.append(StatePatch(path=f"flags.fate_boosts.{event.event_id}", after=boost))
        facts = [f"{actor.actor_id}: {event.kind} {outcome} ({total} vs {payload.difficulty})."]
        if pending:
            facts.append(
                "The GM must adjudicate the cost or alternative outcome; none was applied."
            )
        return RuleResult(
            state=next_state,
            facts=facts,
            rolls=[roll],
            patches=patches,
            resolution=RuleResolution(
                outcome=outcome,
                total=total,
                target=payload.difficulty,
                pending_adjudication=pending,
            ),
            trace=[f"{self.name}@{self.version}.{event.kind}", f"skill={skill}; shifts={shifts}"],
        )


def example_world(world_id: str) -> WorldState:
    return WorldState(
        world_id=world_id,
        actors={
            "player": ActorState(
                actor_id="player",
                stats={
                    "skill:investigate": 2,
                    "skill:athletics": 1,
                    "skill:notice": 3,
                },
            ),
        },
        flags={"fate_aspects": {}, "fate_boosts": {}},
    )
