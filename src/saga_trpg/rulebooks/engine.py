"""Generic rules engine that interprets a rulebook. It contains no system-specific rule."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from pydantic import Field, create_model

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import (
    ActorState,
    RuleResolution,
    RuleResult,
    StatePatch,
    WorldState,
)
from saga_trpg.modules.validation import ClosedPayload, actor_in
from saga_trpg.ports.dice import DicePort
from saga_trpg.rulebooks.expr import Expression, ExpressionError, compile_expression
from saga_trpg.rulebooks.loader import (
    ITEM_KEY,
    BuiltinAction,
    CheckAction,
    InputSpec,
    RulebookSpec,
    StatSpec,
)

_DICE = re.compile(r"^([1-9][0-9]{0,2})d(4|6|8|10|12|20|100)$")
_ROLE_EFFECT = {"attack": "attacker", "check": "check", "save": "save"}


class _FamilyView(Mapping[str, Any]):
    """`actor.proficient[name]` over stats stored as `proficient:<name>`."""

    def __init__(self, actor: ActorState, base: str, spec: StatSpec):
        self.actor, self.base, self.spec = actor, base, spec

    def __getitem__(self, name: str) -> Any:
        if not isinstance(name, str):
            raise ExpressionError(f"{self.base}[...] needs a text key")
        key = f"{self.base}:{name}"
        return _validated(self.actor, key, self.spec, self.actor.stats.get(key, self.spec.default))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str)

    def __iter__(self) -> Iterator[str]:
        prefix = f"{self.base}:"
        return (key[len(prefix) :] for key in self.actor.stats if key.startswith(prefix))

    def __len__(self) -> int:
        return sum(1 for _ in self)


class _ActorView(Mapping[str, Any]):
    """Validated, read-only view of an actor for formulas: hp, max_hp, declared stats."""

    def __init__(self, actor: ActorState, stats: Mapping[str, StatSpec]):
        self.actor = actor
        self.stats = {key: spec for key, spec in stats.items() if not key.endswith(":*")}
        self.families = {key[:-2]: spec for key, spec in stats.items() if key.endswith(":*")}

    def __getitem__(self, key: str) -> Any:
        if key == "hp":
            return self.actor.hp
        if key == "max_hp":
            return self.actor.max_hp
        if key in self.families:
            return _FamilyView(self.actor, key, self.families[key])
        spec = self.stats[key]
        return _validated(self.actor, key, spec, self.actor.stats.get(key, spec.default))

    def __contains__(self, key: object) -> bool:
        return key in ("hp", "max_hp") or key in self.families or key in self.stats

    def __iter__(self) -> Iterator[str]:
        return iter(["hp", "max_hp", *self.families, *self.stats])

    def __len__(self) -> int:
        return 2 + len(self.families) + len(self.stats)


class _ModView(Mapping[str, int]):
    """`mod[stat]`: the stat's modifier formula applied to the actor's validated value."""

    def __init__(self, view: _ActorView, modifiers: Mapping[str, Expression]):
        self.view, self.modifiers = view, modifiers

    def __getitem__(self, key: str) -> int:
        value = self.view[key]
        return int(self.modifiers[key].evaluate({"value": value}))

    def __contains__(self, key: object) -> bool:
        return key in self.modifiers

    def __iter__(self) -> Iterator[str]:
        return iter(self.modifiers)

    def __len__(self) -> int:
        return len(self.modifiers)


def _validated(actor: ActorState, key: str, spec: StatSpec, value: Any) -> Any:
    label = f"{actor.actor_id}.{key}"
    if value is None:
        raise ValueError(f"{label} is missing")
    if spec.type == "integer":
        if type(value) is not int or not spec.range[0] <= value <= spec.range[1]:
            raise ValueError(f"{label} must be an integer in {spec.range[0]}..{spec.range[1]}")
    elif spec.type == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{label} must be true or false")
    else:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be text")
        if spec.enum is not None and value not in spec.enum:
            raise ValueError(f"{label} must be one of {', '.join(spec.enum)}")
        if spec.pattern is not None and not re.fullmatch(spec.pattern, value):
            raise ValueError(f"{label} must match {spec.pattern}")
    return value


def _input_field(spec: InputSpec) -> tuple[Any, Any]:
    if spec.type == "integer":
        annotation, default = int, ...
        field = Field(ge=spec.range[0], le=spec.range[1])
    elif spec.type == "actor":
        annotation, default, field = str, ..., Field(min_length=1)
    elif spec.type == "boolean":
        return bool, bool(spec.default)
    elif spec.enum is not None:
        annotation, default, field = Literal[tuple(spec.enum)], ..., Field()
    else:
        annotation, default, field = str, ..., Field(pattern=spec.pattern)
    if spec.optional:
        annotation, default = annotation | None, None
    return annotation, (field if default is ... else Field(default=default))


class DataRules:
    """A `RuleModule` whose behaviour is entirely described by a `RulebookSpec`."""

    def __init__(self, spec: RulebookSpec) -> None:
        self.spec = spec
        self.name = spec.id
        self.version = spec.version
        self.modifiers = {
            key: compile_expression(stat.modifier)
            for key, stat in spec.stats.items()
            if stat.modifier is not None
        }
        self.formulas: dict[str, dict[str, Expression]] = {}
        self.outcome_rules = {
            key: [(compile_expression(rule.when), rule.outcome) for rule in rules]
            for key, rules in spec.outcomes.items()
        }
        self.payload_models: dict[str, type[ClosedPayload]] = {}
        for kind, action in spec.actions.items():
            self.payload_models[kind] = self._payload_model(kind, action)
            if isinstance(action, CheckAction):
                formulas = {
                    "modifier": compile_expression(action.modifier),
                    "target": compile_expression(action.target),
                }
                for index, requirement in enumerate(action.requires):
                    formulas[f"requires.{index}"] = compile_expression(requirement.when)
                if action.on_hit is not None:
                    formulas["on_hit.dice"] = compile_expression(action.on_hit.dice)
                    formulas["on_hit.bonus"] = compile_expression(action.on_hit.bonus)
                self.formulas[kind] = formulas

    # -- payload models --------------------------------------------------------------------

    def _payload_model(self, kind: str, action: CheckAction | BuiltinAction) -> type[ClosedPayload]:
        fields: dict[str, Any] = {}
        if isinstance(action, CheckAction):
            for name, spec in action.input.items():
                fields[name] = _input_field(spec)
            if self.spec.checks[action.check].advantage == "roll_twice":
                fields["advantage"] = (bool, False)
                fields["disadvantage"] = (bool, False)
        else:
            builtin = action.builtin
            if builtin != "time_advance":
                fields["target_id"] = (str, Field(min_length=1))
            if builtin == "hp_change" or builtin.startswith("inventory_"):
                fields["amount"] = (int, Field(ge=1, le=1000))
            if builtin.startswith("condition_"):
                fields["condition"] = (Literal[tuple(self.spec.conditions)], ...)
            if builtin.startswith("inventory_"):
                fields["item"] = (
                    (Literal[tuple(self.spec.items)], ...)
                    if self.spec.items
                    else (str, Field(pattern=ITEM_KEY))
                )
            if builtin == "time_advance":
                fields["amount"] = (int, Field(ge=1, le=100_000))
                fields["unit"] = (Literal[tuple(action.units or {})], ...)
        title = "".join(part.title() for part in kind.split("_"))
        return create_model(title, __base__=ClosedPayload, **fields)

    # -- worlds ------------------------------------------------------------------------------

    def example_world(self, world_id: str) -> WorldState:
        actors = {}
        if self.spec.example_world is not None:
            for actor_id, actor in self.spec.example_world.actors.items():
                actors[actor_id] = ActorState(
                    actor_id=actor_id,
                    hp=actor.hp,
                    max_hp=actor.max_hp,
                    stats=dict(actor.stats),
                    inventory=dict(actor.inventory),
                    conditions=set(actor.conditions),
                )
        return WorldState(world_id=world_id, actors=actors)

    # -- dispatch ----------------------------------------------------------------------------

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult:
        if not event.event_id:
            raise ValueError("event id must be assigned before dispatch")
        action = self.spec.actions.get(event.kind)
        if action is None:
            raise ValueError(f"unsupported {self.name} action: {event.kind}")
        payload = self.payload_models[event.kind].model_validate(event.payload)
        actor = actor_in(state, event.actor_id)
        if isinstance(action, BuiltinAction):
            return self._builtin(state, event, action, payload)
        return self._check(state, event, action, payload, actor, dice)

    def _hp_sane(self, actor: ActorState) -> None:
        if not 0 <= actor.hp <= actor.max_hp or actor.max_hp <= 0:
            raise ValueError(f"{actor.actor_id} has invalid HP")

    def _trace(self, event: GameEvent, *lines: str) -> list[str]:
        return [f"{self.name}@{self.version}.{event.kind}", *lines]

    def _check(  # noqa: C901
        self,
        state: WorldState,
        event: GameEvent,
        action: CheckAction,
        payload: ClosedPayload,
        actor: ActorState,
        dice: DicePort,
    ) -> RuleResult:
        formulas = self.formulas[event.kind]
        data = payload.model_dump()
        target: ActorState | None = None
        if (
            "target_id" in data
            and action.input.get("target_id", InputSpec(type="text")).type == "actor"
        ):
            target = actor_in(state, data["target_id"])
        self._hp_sane(actor)
        if target is not None:
            self._hp_sane(target)
        for condition in sorted(actor.conditions):
            if self.spec.conditions.get(condition) and self.spec.conditions[condition].cannot_act:
                raise ValueError(f"{actor.actor_id} cannot act while {condition}")
        actor_view = _ActorView(actor, self.spec.stats)
        scope: dict[str, Any] = {
            "actor": actor_view,
            "mod": _ModView(actor_view, self.modifiers),
            "input": {key: ("" if value is None else value) for key, value in data.items()},
        }
        if target is not None:
            target_view = _ActorView(target, self.spec.stats)
            scope["target"] = target_view
            scope["target_mod"] = _ModView(target_view, self.modifiers)
        try:
            for index, requirement in enumerate(action.requires):
                if not formulas[f"requires.{index}"].evaluate(scope):
                    raise ValueError(requirement.error)
            modifier = _as_int(formulas["modifier"].evaluate(scope), "modifier")
            target_value = _as_int(formulas["target"].evaluate(scope), "target")
            damage_dice: tuple[int, int] | None = None
            bonus = 0
            if action.on_hit is not None:
                expression = formulas["on_hit.dice"].evaluate(scope)
                match = _DICE.fullmatch(expression) if isinstance(expression, str) else None
                if match is None:
                    raise ValueError(f"on_hit dice {expression!r} must look like 2d6")
                damage_dice = (int(match[1]), int(match[2]))
                bonus = _as_int(formulas["on_hit.bonus"].evaluate(scope), "on_hit.bonus")
        except ExpressionError as exc:
            raise ValueError(str(exc)) from None

        # Everything above is validated before the first tool call, including on a miss.
        check = self.spec.checks[action.check]
        sources: list[tuple[str, str]] = []
        if check.advantage == "roll_twice":
            if data.get("advantage"):
                sources.append(("advantage", "input"))
            if data.get("disadvantage"):
                sources.append(("disadvantage", "input"))
            effect = _ROLE_EFFECT[action.role]
            for condition in sorted(actor.conditions):
                spec = self.spec.conditions.get(condition)
                value = getattr(spec, effect, None) if spec else None
                if value:
                    sources.append((value, f"{condition} (actor)"))
            if target is not None and action.role == "attack":
                for condition in sorted(target.conditions):
                    spec = self.spec.conditions.get(condition)
                    if spec and spec.target:
                        sources.append((spec.target, f"{condition} (target)"))
        advantage = any(kind == "advantage" for kind, _ in sources)
        disadvantage = any(kind == "disadvantage" for kind, _ in sources)
        count = 2 if advantage != disadvantage else 1
        rolls = [
            dice.roll(
                world_id=state.world_id,
                event_id=event.event_id,
                roll_key=f"{event.kind}:{index}",
                expression=check.roll,
            )
            for index in range(count)
        ]
        values = [roll.total for roll in rolls]
        selected = max(values) if advantage and not disadvantage else min(values)
        total = selected + modifier
        outcome = None
        for rule, name in self.outcome_rules[action.outcomes]:
            if rule.evaluate({"die": selected, "total": total, "target": target_value}):
                outcome = name
                break
        if outcome is None:
            raise ValueError(f"no outcome rule in {action.outcomes!r} matched")

        next_state = state.model_copy(deep=True)
        patches: list[StatePatch] = []
        damage: int | None = 0 if action.role == "attack" else None
        if damage_dice is not None and target is not None and outcome in ("hit", "critical_hit"):
            number, faces = damage_dice
            if outcome == "critical_hit" and action.on_hit.critical == "double_dice":
                number *= 2
            rolled = dice.roll(
                world_id=state.world_id,
                event_id=event.event_id,
                roll_key="damage",
                expression=f"{number}d{faces}{bonus:+d}",
            )
            rolls.append(rolled)
            damage = max(0, rolled.total)
            remaining = max(0, target.hp - damage)
            next_state.actors[target.actor_id].hp = remaining
            patches.append(
                StatePatch(path=f"actors.{target.actor_id}.hp", before=target.hp, after=remaining)
            )
        subject = f" against {target.actor_id}" if target is not None else ""
        facts = [f"{event.actor_id}: {event.kind} {outcome}{subject} ({total} vs {target_value})."]
        if damage:
            remaining_hp = next_state.actors[target.actor_id].hp
            facts.append(f"{target.actor_id} takes {damage} damage; HP {remaining_hp}.")
        trace = self._trace(
            event,
            f"die={selected}; modifier={modifier}; rolls={count}",
            *[f"{kind}: {source}" for kind, source in sources],
        )
        return RuleResult(
            state=next_state,
            rolls=rolls,
            patches=patches,
            resolution=RuleResolution(
                outcome=outcome, total=total, target=target_value, damage=damage
            ),
            facts=facts,
            trace=trace,
        )

    def _builtin(
        self, state: WorldState, event: GameEvent, action: BuiltinAction, payload: ClosedPayload
    ) -> RuleResult:
        data = payload.model_dump()
        next_state = state.model_copy(deep=True)
        if action.builtin == "time_advance":
            seconds = data["amount"] * action.units[data["unit"]]
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
                    f"{event.actor_id}: {event.kind} {data['amount']} {data['unit']}(s); "
                    f"simulated time {next_state.simulated_time}s."
                ],
                trace=self._trace(event, f"seconds={seconds}"),
            )
        target = actor_in(state, data["target_id"])
        self._hp_sane(target)
        changed = next_state.actors[target.actor_id]
        if action.builtin == "hp_change":
            after = target.hp + action.sign * data["amount"]
            after = max(0, min(target.max_hp, after))
            changed.hp = after
            patch = StatePatch(path=f"actors.{target.actor_id}.hp", before=target.hp, after=after)
            fact = (
                f"{event.actor_id}: {event.kind} {data['amount']} to {target.actor_id}; "
                f"HP {target.hp}->{after}/{target.max_hp}."
            )
        elif action.builtin.startswith("condition_"):
            condition = data["condition"]
            present = condition in target.conditions
            if action.builtin == "condition_add" and present:
                raise ValueError(f"{target.actor_id} already has condition {condition}")
            if action.builtin == "condition_remove" and not present:
                raise ValueError(f"{target.actor_id} does not have condition {condition}")
            if action.builtin == "condition_add":
                changed.conditions.add(condition)
            else:
                changed.conditions.remove(condition)
            patch = StatePatch(
                path=f"actors.{target.actor_id}.conditions",
                before=sorted(target.conditions),
                after=sorted(changed.conditions),
            )
            fact = (
                f"{event.actor_id}: {event.kind} {condition} on {target.actor_id}; "
                f"conditions {sorted(changed.conditions)}."
            )
        else:
            item, amount = data["item"], data["amount"]
            before = target.inventory.get(item, 0)
            if action.builtin == "inventory_remove" and before < amount:
                raise ValueError(f"{target.actor_id} holds {before} {item}, cannot take {amount}")
            after = before + amount if action.builtin == "inventory_add" else before - amount
            if after > 0:
                changed.inventory[item] = after
            else:
                del changed.inventory[item]
            patch = StatePatch(
                path=f"actors.{target.actor_id}.inventory.{item}", before=before, after=after
            )
            fact = (
                f"{event.actor_id}: {event.kind} {amount} {item} "
                f"{'to' if action.builtin == 'inventory_add' else 'from'} {target.actor_id}; "
                f"now {after}."
            )
        return RuleResult(
            state=next_state,
            patches=[patch],
            facts=[fact],
            trace=self._trace(event, f"builtin={action.builtin}"),
        )


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must evaluate to an integer, got {value!r}")
    return value
