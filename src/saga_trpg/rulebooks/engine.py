"""Generic rules engine that interprets a rulebook. It contains no system-specific rule."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from pydantic import Field, create_model

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.mapgen import generate_map
from saga_trpg.domain.maps import EXIT_KEY
from saga_trpg.domain.models import (
    ActorState,
    MapState,
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
    ExampleMap,
    InputSpec,
    RecordField,
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
        if key == "id":
            return self.actor.actor_id
        if key == "hp":
            return self.actor.hp
        if key == "max_hp":
            return self.actor.max_hp
        if key in self.families:
            return _FamilyView(self.actor, key, self.families[key])
        spec = self.stats[key]
        return _validated(self.actor, key, spec, self.actor.stats.get(key, spec.default))

    def __contains__(self, key: object) -> bool:
        return key in ("id", "hp", "max_hp") or key in self.families or key in self.stats

    def __iter__(self) -> Iterator[str]:
        return iter(["id", "hp", "max_hp", *self.families, *self.stats])

    def __len__(self) -> int:
        return 3 + len(self.families) + len(self.stats)


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
    constraints: dict[str, Any] = {}
    if spec.type == "integer":
        annotation = int
        constraints = {"ge": spec.range[0], "le": spec.range[1]}
    elif spec.type == "actor":
        annotation = str
        constraints = {"min_length": 1}
    elif spec.type == "boolean":
        annotation = bool
    elif spec.enum is not None:
        annotation = Literal[tuple(spec.enum)]
    else:
        annotation = str
        constraints = {"pattern": spec.pattern}
    if spec.default is not None:
        return annotation, Field(default=spec.default, **constraints)
    if not spec.optional:
        return annotation, Field(**constraints)
    if spec.type == "boolean":
        return bool, Field(default=False, **constraints)
    return annotation | None, Field(default=None, **constraints)


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
            key: [(compile_expression(rule.when), rule.outcome, rule.pending) for rule in rules]
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
                    source = requirement.when or requirement.unique.value
                    formulas[f"requires.{index}"] = compile_expression(source)
                for outcome, effects in action.on_outcome.items():
                    for index, effect in enumerate(effects):
                        for name, formula in effect.fields.items():
                            formulas[f"on_outcome.{outcome}.{index}.{name}"] = compile_expression(
                                formula
                            )
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
            if builtin not in ("time_advance", "map_move"):
                fields["target_id"] = (str, Field(min_length=1))
            if builtin == "map_move":
                fields["exit"] = (str, Field(pattern=EXIT_KEY))
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
            if builtin.startswith("record_"):
                fields.pop("target_id", None)
                record = self.spec.records[action.record]
                key_field = record.fields[record.unique[0]]
                if key_field.type == "boolean":
                    fields[record.unique[0]] = (bool, Field())
                else:
                    fields[record.unique[0]] = _input_field(
                        InputSpec(
                            type="integer" if key_field.type == "integer" else "text",
                            range=key_field.range,
                            enum=key_field.enum,
                            pattern=key_field.pattern
                            or (".+" if key_field.type == "text" else None),
                        )
                    )
                if builtin == "record_adjust":
                    fields["amount"] = (int, Field(default=1, ge=1, le=1000))
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
        flags = dict(self.spec.example_world.flags) if self.spec.example_world else {}
        state = WorldState(world_id=world_id, actors=actors, flags=flags)
        spec_map = self.spec.example_world.map if self.spec.example_world else None
        if spec_map is not None:
            state.map = _example_map(spec_map, world_id)
            for actor_id, actor in state.actors.items():
                room_id = spec_map.placements.get(actor_id, state.map.start_room)
                actor.location = room_id
                actor.visited_rooms = {room_id}
        return state

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
        for collection, record in self.spec.records.items():
            existing = state.flags.get(collection, {})
            if not isinstance(existing, dict):
                raise ValueError(f"flags.{collection} must hold structured records")
            for key, item in existing.items():
                _validate_record(f"flags.{collection}.{key}", item, record.fields)
        try:
            for index, requirement in enumerate(action.requires):
                value = formulas[f"requires.{index}"].evaluate(scope)
                if requirement.unique is not None:
                    rule = requirement.unique
                    field = self.spec.records[rule.record].fields[rule.field]
                    _validate_field(f"requires[{index}].unique.{rule.field}", value, field)
                    taken = any(
                        item.get(rule.field) == value
                        for item in state.flags.get(rule.record, {}).values()
                    )
                    if taken:
                        raise ValueError(f"{requirement.error} ({rule.field} {value!r} exists)")
                elif not value:
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
                doubled = 2 if action.on_hit.critical == "double_dice" else 1
                if damage_dice[0] * doubled > 100:
                    raise ValueError(
                        f"on_hit dice {expression!r} cannot be rolled after a critical "
                        "(at most 100 dice)"
                    )
                bonus = _as_int(formulas["on_hit.bonus"].evaluate(scope), "on_hit.bonus")
        except ExpressionError as exc:
            raise ValueError(str(exc)) from None

        # Records an outcome would write depend only on actor and input, so build them now and
        # refuse a unique-field collision before any die is rolled.
        planned: dict[str, list[dict[str, Any]]] = {}
        try:
            for outcome_name, effects in action.on_outcome.items():
                planned[outcome_name] = []
                for index, effect in enumerate(effects):
                    record = {
                        name: formulas[f"on_outcome.{outcome_name}.{index}.{name}"].evaluate(scope)
                        for name in effect.fields
                    }
                    collection = self.spec.records[effect.record]
                    _validate_record(
                        f"on_outcome.{outcome_name}[{index}]", record, collection.fields
                    )
                    for unique in collection.unique:
                        if any(
                            item.get(unique) == record[unique]
                            for item in state.flags.get(effect.record, {}).values()
                        ):
                            raise ValueError(
                                f"{effect.record} already has {unique} {record[unique]!r}"
                            )
                    planned[outcome_name].append(record)
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
        outcome, pending = None, False
        for rule, name, flagged in self.outcome_rules[action.outcomes]:
            if rule.evaluate({"die": selected, "total": total, "target": target_value}):
                outcome, pending = name, flagged
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
        for effect, record in zip(
            action.on_outcome.get(outcome, []), planned.get(outcome, []), strict=True
        ):
            next_state.flags.setdefault(effect.record, {})[event.event_id] = record
            patches.append(StatePatch(path=f"flags.{effect.record}.{event.event_id}", after=record))
        subject = f" against {target.actor_id}" if target is not None else ""
        facts = [f"{event.actor_id}: {event.kind} {outcome}{subject} ({total} vs {target_value})."]
        if pending:
            facts.append(
                "The GM must adjudicate the cost or alternative outcome; none was applied."
            )
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
                outcome=outcome,
                total=total,
                target=target_value,
                damage=damage,
                pending_adjudication=pending,
            ),
            facts=facts,
            trace=trace,
        )

    def _record(
        self,
        state: WorldState,
        next_state: WorldState,
        event: GameEvent,
        action: BuiltinAction,
        data: dict[str, Any],
    ) -> RuleResult:
        collection = self.spec.records[action.record]
        stored_all = state.flags.get(action.record, {})
        if not isinstance(stored_all, dict):
            raise ValueError(f"flags.{action.record} must hold structured records")
        for key, item in stored_all.items():
            _validate_record(f"flags.{action.record}.{key}", item, collection.fields)
        unique = collection.unique[0]
        key = _find_record(state, action.record, unique, data[unique])
        if key is None:
            raise ValueError(f"no {action.record} record with {unique} {data[unique]!r}")
        stored = stored_all[key]
        if action.builtin == "record_remove":
            del next_state.flags[action.record][key]
            return RuleResult(
                state=next_state,
                patches=[
                    StatePatch(path=f"flags.{action.record}.{key}", before=stored, after=None)
                ],
                facts=[
                    f"{event.actor_id}: {event.kind} {data[unique]!r} removed from {action.record}."
                ],
                trace=self._trace(event, f"builtin=record_remove; key={key}"),
            )
        field = collection.fields[action.field]
        before = stored[action.field]
        after = before + action.sign * data["amount"]
        if not field.range[0] <= after <= field.range[1]:
            raise ValueError(
                f"{action.record} {data[unique]!r}: {action.field} would be {after}, "
                f"outside {field.range[0]}..{field.range[1]}"
            )
        if action.field in collection.unique and any(
            other_key != key and item.get(action.field) == after
            for other_key, item in stored_all.items()
        ):
            raise ValueError(f"{action.record} already has {action.field} {after!r}")
        next_state.flags[action.record][key][action.field] = after
        return RuleResult(
            state=next_state,
            patches=[
                StatePatch(
                    path=f"flags.{action.record}.{key}.{action.field}", before=before, after=after
                )
            ],
            facts=[
                f"{event.actor_id}: {event.kind} {data[unique]!r}; "
                f"{action.field} {before}->{after}."
            ],
            trace=self._trace(event, f"builtin=record_adjust; key={key}"),
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
        if action.builtin.startswith("record_"):
            return self._record(state, next_state, event, action, data)
        if action.builtin == "map_move":
            return self._move(state, next_state, event, data)
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

    def _move(
        self, state: WorldState, next_state: WorldState, event: GameEvent, data: dict[str, Any]
    ) -> RuleResult:
        # The exit key is resolved against the actor's room in this state, at apply time, so
        # command parsing stays stateless and replay reads the recorded result (ADR 0009).
        if state.map is None:
            raise ValueError("this world has no map")
        actor = actor_in(state, event.actor_id)
        if actor.location is None:
            raise ValueError(f"{actor.actor_id} is not on the map")
        room = state.map.rooms[actor.location]
        exit_ = room.exits.get(data["exit"])
        if exit_ is None:
            known = ", ".join(sorted(room.exits)) or "none"
            raise ValueError(f"{room.name} has no exit {data['exit']!r}; exits: {known}")
        moved = next_state.actors[actor.actor_id]
        moved.location = exit_.to_room
        moved.visited_rooms.add(exit_.to_room)
        destination = state.map.rooms[exit_.to_room]
        return RuleResult(
            state=next_state,
            patches=[
                StatePatch(
                    path=f"actors.{actor.actor_id}.location",
                    before=actor.location,
                    after=exit_.to_room,
                ),
                StatePatch(
                    path=f"actors.{actor.actor_id}.visited_rooms",
                    before=sorted(actor.visited_rooms),
                    after=sorted(moved.visited_rooms),
                ),
            ],
            facts=[
                f"{actor.actor_id}: {event.kind} through {exit_.label} "
                f"from {room.name} to {destination.name}."
            ],
            trace=self._trace(
                event, f"builtin=map_move; exit={data['exit']}; {actor.location}->{exit_.to_room}"
            ),
        )


def _example_map(spec_map: ExampleMap, world_id: str) -> MapState:
    if spec_map.generate is not None:
        low, high = spec_map.generate.rooms
        # Each world (a RisuAI chat) gets its own dungeon; the same world always the same one.
        return generate_map(f"{spec_map.generate.seed}|{world_id}", min_rooms=low, max_rooms=high)
    return MapState(
        start_room=spec_map.start_room,
        rooms={room_id: room.model_copy(deep=True) for room_id, room in spec_map.rooms.items()},
    )


def _find_record(state: WorldState, collection: str, field: str, value: Any) -> str | None:
    stored = state.flags.get(collection, {})
    if not isinstance(stored, dict):
        return None
    matches = [
        key
        for key, item in stored.items()
        if isinstance(item, dict)
        and item.get(field) == value
        and type(item.get(field)) is type(value)
    ]
    if len(matches) > 1:
        raise ValueError(f"{collection} has more than one record with {field} {value!r}")
    return matches[0] if matches else None


def _validate_field(label: str, value: Any, field: RecordField) -> None:
    if field.type == "integer":
        if type(value) is not int or not field.range[0] <= value <= field.range[1]:
            raise ValueError(f"{label}: must be an integer in range")
    elif field.type == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{label}: must be true or false")
    else:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label}: must be non-empty text")
        if field.enum is not None and value not in field.enum:
            raise ValueError(f"{label}: must be one of {', '.join(field.enum)}")
        if field.pattern is not None and not re.fullmatch(field.pattern, value):
            raise ValueError(f"{label}: must match {field.pattern}")


def _validate_record(label: str, item: Any, fields: Mapping[str, RecordField]) -> None:
    if not isinstance(item, dict) or set(item) != set(fields):
        raise ValueError(f"{label}: record must have exactly the fields {', '.join(fields)}")
    for name, field in fields.items():
        _validate_field(f"{label}.{name}", item[name], field)


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must evaluate to an integer, got {value!r}")
    return value
