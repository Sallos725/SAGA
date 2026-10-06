"""Rulebook schema v1 and its loader.

A rulebook is one JSON document a creator fills in: stats, check frames, outcome grades,
actions, conditions, items, an example world and narration text. Validation reports the
offending entry by path in plain language so an author can fix it without reading code.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from saga_trpg.domain.models import RuleResolution
from saga_trpg.rulebooks.expr import ExpressionError, compile_expression

KEY = r"^[a-z][a-z0-9_]{0,39}$"
FAMILY_KEY = r"^[a-z][a-z0-9_]{0,39}:\*$"
VERSION = r"^[0-9]+\.[0-9]+\.[0-9]+$"
ITEM_KEY = r"^[a-z0-9_]{1,40}$"
OUTCOMES = tuple(RuleResolution.model_fields["outcome"].annotation.__args__)
Advantage = Literal["advantage", "disadvantage"]
Role = Literal["attack", "check", "save"]
Builtin = Literal[
    "hp_change",
    "condition_add",
    "condition_remove",
    "time_advance",
    "inventory_add",
    "inventory_remove",
]


class RulebookError(ValueError):
    """A rulebook file is malformed; the message names the entry and what is wrong."""


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class License(Closed):
    id: str = Field(min_length=1)
    url: str | None = None
    attribution: str


class Source(Closed):
    url: str
    revision: str


class StatSpec(Closed):
    type: Literal["integer", "boolean", "text"]
    range: tuple[int, int] | None = None
    modifier: str | None = None
    enum: list[str] | None = None
    pattern: str | None = None
    default: int | bool | str | None = None


class CheckSpec(Closed):
    # Schema v1 advertises exactly what the standard dice path can roll (1..100 dice).
    roll: str = Field(pattern=r"^([1-9]|[1-9][0-9]|100)d(3|4|6|8|10|12|20|100)([+-][0-9]{1,3})?$")
    advantage: Literal["roll_twice", "none"] = "none"


class OutcomeRule(Closed):
    when: str
    outcome: str
    pending: bool = False


class InputSpec(Closed):
    type: Literal["integer", "text", "actor", "boolean"]
    range: tuple[int, int] | None = None
    enum: list[str] | None = None
    pattern: str | None = None
    optional: bool = False
    default: bool | int | str | None = None


class UniqueRule(Closed):
    record: str
    field: str
    value: str


class Requirement(Closed):
    """Either a formula that must be true or a uniqueness rule over a record collection."""

    when: str | None = None
    unique: UniqueRule | None = None
    error: str = Field(min_length=1)


class RecordField(Closed):
    type: Literal["integer", "text", "actor", "boolean"]
    range: tuple[int, int] | None = None
    enum: list[str] | None = None
    pattern: str | None = None


class RecordSpec(Closed):
    """A collection in `flags[<name>]`, keyed by event id, with typed fields."""

    fields: dict[str, RecordField]
    unique: list[str] = Field(default_factory=list)


class RecordEffect(Closed):
    record: str
    fields: dict[str, str]


class OnHit(Closed):
    dice: str
    bonus: str = "0"
    critical: Literal["double_dice", "none"] = "double_dice"


class CheckAction(Closed):
    role: Role
    input: dict[str, InputSpec] = Field(default_factory=dict)
    requires: list[Requirement] = Field(default_factory=list)
    check: str
    modifier: str = "0"
    target: str
    outcomes: str
    on_hit: OnHit | None = None
    on_outcome: dict[str, list[RecordEffect]] = Field(default_factory=dict)


class BuiltinAction(Closed):
    builtin: Builtin
    sign: Literal[1, -1] | None = None
    units: dict[str, int] | None = None


class ConditionSpec(Closed):
    attacker: Advantage | None = None
    target: Advantage | None = None
    check: Advantage | None = None
    save: Advantage | None = None
    cannot_act: bool = False


class ItemSpec(Closed):
    name: str = Field(min_length=1)


class ExampleActor(Closed):
    hp: int = 10
    max_hp: int = 10
    stats: dict[str, int | bool | str] = Field(default_factory=dict)
    inventory: dict[str, int] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)


class ExampleWorld(Closed):
    actors: dict[str, ExampleActor]
    flags: dict[str, Any] = Field(default_factory=dict)


class DisplaySpec(Closed):
    """What a host status footer shows; the backend renders it, the host only prints it."""

    actor: list[str] = Field(default_factory=lambda: ["hp", "conditions", "inventory"])
    records: list[str] = Field(default_factory=list)
    world: list[str] = Field(default_factory=lambda: ["time"])


class RulebookSpec(Closed):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,60}$")
    version: str = Field(pattern=VERSION)
    title: str = Field(min_length=1)
    license: License
    source: Source | None = None
    modifications: str = ""
    stats: dict[str, StatSpec] = Field(default_factory=dict)
    checks: dict[str, CheckSpec] = Field(default_factory=dict)
    outcomes: dict[str, list[OutcomeRule]] = Field(default_factory=dict)
    records: dict[str, RecordSpec] = Field(default_factory=dict)
    actions: dict[str, CheckAction | BuiltinAction]
    conditions: dict[str, ConditionSpec] = Field(default_factory=dict)
    items: dict[str, ItemSpec] = Field(default_factory=dict)
    example_world: ExampleWorld | None = None
    display: DisplaySpec = Field(default_factory=DisplaySpec)
    implemented: list[str] = Field(default_factory=list)
    not_implemented: list[str] = Field(default_factory=list)
    narration: str = ""


_BUILTIN_PAYLOAD_INPUTS = {
    "hp_change": {"target_id", "amount"},
    "condition_add": {"target_id", "condition"},
    "condition_remove": {"target_id", "condition"},
    "time_advance": {"amount", "unit"},
    "inventory_add": {"target_id", "item", "amount"},
    "inventory_remove": {"target_id", "item", "amount"},
}


def _bundled_dir() -> Path:
    return Path(str(resources.files("saga_trpg.rulebooks") / "bundled"))


def bundled_versions(rulebook_id: str) -> list[str]:
    """Every bundled version of a rulebook, oldest first. Bundled files are immutable: a
    change to the rules is a new `<id>@<version>.json`, never an edit in place."""
    prefix = f"{rulebook_id}@"
    found = [
        path.name[len(prefix) : -5]
        for path in _bundled_dir().glob(f"{rulebook_id}@*.json")
        if path.name.startswith(prefix)
    ]
    return sorted(found, key=lambda v: tuple(int(part) for part in v.split(".")))


def rulebook_path(rulebook_id: str, version: str | None = None) -> Path:
    """Path of a bundled rulebook: a specific version, or the latest one."""
    if version is None:
        versions = bundled_versions(rulebook_id)
        if not versions:
            raise RulebookError(f"no bundled rulebook named {rulebook_id!r}")
        version = versions[-1]
    return _bundled_dir() / f"{rulebook_id}@{version}.json"


def load_rulebook(path: Path | str) -> RulebookSpec:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RulebookError(f"{path}: file not found") from None
    except json.JSONDecodeError as exc:
        raise RulebookError(f"{path}: not valid JSON at line {exc.lineno}: {exc.msg}") from None
    return parse_rulebook(data)


def parse_rulebook(data: Any) -> RulebookSpec:
    try:
        spec = RulebookSpec.model_validate(data)
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"] if part != "function-after")
            problems.append(f"{location}: {error['msg']}")
        raise RulebookError("; ".join(problems)) from None
    problems = list(_cross_check(spec))
    if problems:
        raise RulebookError("; ".join(problems))
    return spec


def _check_formula(location: str, source: str, allowed: set[str]) -> list[str]:
    try:
        expression = compile_expression(source)
    except ExpressionError as exc:
        return [f"{location}: {exc}"]
    unknown = sorted(expression.names - allowed)
    if unknown:
        names, permitted = ", ".join(unknown), ", ".join(sorted(allowed))
        return [f"{location}: unknown name(s) {names}; allowed: {permitted}"]
    return []


def _cross_check(spec: RulebookSpec):  # noqa: C901
    for key, stat in spec.stats.items():
        if not (re.fullmatch(KEY, key) or re.fullmatch(FAMILY_KEY, key)):
            yield f"stats.{key}: key must match {KEY} or end with ':*' for a family"
        if stat.type == "integer":
            if stat.range is None:
                yield f"stats.{key}.range: integer stats need a [min, max] range"
            elif stat.range[0] > stat.range[1]:
                yield f"stats.{key}.range: min is greater than max"
            if stat.modifier is not None:
                yield from _check_formula(f"stats.{key}.modifier", stat.modifier, {"value"})
        else:
            if stat.range is not None or stat.modifier is not None:
                yield f"stats.{key}: range and modifier apply to integer stats only"
        if stat.type == "text":
            if stat.enum is None and stat.pattern is None:
                yield f"stats.{key}: text stats need an enum or a pattern"
            if stat.pattern is not None:
                try:
                    re.compile(stat.pattern)
                except re.error as exc:
                    yield f"stats.{key}.pattern: {exc}"
        elif stat.enum is not None or stat.pattern is not None:
            yield f"stats.{key}: enum and pattern apply to text stats only"
        if stat.default is not None:
            if _kind_of(stat.default) != stat.type:
                yield f"stats.{key}.default: must be a {stat.type}"
            else:
                yield from _check_default(f"stats.{key}.default", stat)
    for key in spec.checks:
        if not re.fullmatch(KEY, key):
            yield f"checks.{key}: key must match {KEY}"
    for key, rules in spec.outcomes.items():
        if not re.fullmatch(KEY, key):
            yield f"outcomes.{key}: key must match {KEY}"
        if not rules:
            yield f"outcomes.{key}: an outcome table needs at least one rule"
        for index, rule in enumerate(rules):
            if rule.outcome not in OUTCOMES:
                yield (
                    f"outcomes.{key}[{index}].outcome: {rule.outcome!r} is not one of "
                    f"{', '.join(OUTCOMES)}"
                )
            yield from _check_formula(
                f"outcomes.{key}[{index}].when", rule.when, {"die", "total", "target"}
            )
    for key in spec.conditions:
        if not re.fullmatch(KEY, key):
            yield f"conditions.{key}: key must match {KEY}"
    for key, record in spec.records.items():
        if not re.fullmatch(KEY, key):
            yield f"records.{key}: key must match {KEY}"
        for name, field in record.fields.items():
            if not re.fullmatch(KEY, name):
                yield f"records.{key}.fields.{name}: key must match {KEY}"
            if _reserved_name(name):
                yield f"records.{key}.fields.{name}: this name is reserved; choose another"
            yield from _check_typed(f"records.{key}.fields.{name}", field)
        for name in record.unique:
            if name not in record.fields:
                yield f"records.{key}.unique: {name!r} is not a field of this record"
    for key in spec.items:
        if not re.fullmatch(ITEM_KEY, key):
            yield f"items.{key}: key must match {ITEM_KEY}"
    for key, action in spec.actions.items():
        if not re.fullmatch(KEY, key):
            yield f"actions.{key}: key must match {KEY}"
        if isinstance(action, BuiltinAction):
            if action.builtin == "hp_change" and action.sign is None:
                yield f"actions.{key}.sign: hp_change needs sign 1 (heal) or -1 (damage)"
            if action.builtin == "time_advance":
                if not action.units:
                    yield f"actions.{key}.units: time_advance needs a unit → seconds map"
                else:
                    for unit, seconds in action.units.items():
                        if not re.fullmatch(KEY, unit) or seconds < 1:
                            yield f"actions.{key}.units.{unit}: unit keys match {KEY}, seconds >= 1"
            if action.builtin in ("condition_add", "condition_remove") and not spec.conditions:
                yield f"actions.{key}: {action.builtin} needs at least one entry in conditions"
            continue
        if action.check not in spec.checks:
            defined = ", ".join(spec.checks) or "none"
            yield f"actions.{key}.check: unknown check {action.check!r}; defined: {defined}"
        if action.outcomes not in spec.outcomes:
            defined = ", ".join(spec.outcomes) or "none"
            yield (
                f"actions.{key}.outcomes: unknown outcome table {action.outcomes!r}; "
                f"defined: {defined}"
            )
        has_target = any(
            name == "target_id" and item.type == "actor" for name, item in action.input.items()
        )
        for name, item in action.input.items():
            if not re.fullmatch(KEY, name):
                yield f"actions.{key}.input.{name}: key must match {KEY}"
            if _reserved_name(name):
                yield f"actions.{key}.input.{name}: this name is reserved; choose another"
            if name in ("advantage", "disadvantage"):
                yield (
                    f"actions.{key}.input.{name}: reserved; set the check frame's advantage instead"
                )
            if item.type == "integer" and item.range is None:
                yield f"actions.{key}.input.{name}.range: integer inputs need a [min, max] range"
            if item.type == "integer" and item.range is not None and item.range[0] > item.range[1]:
                yield f"actions.{key}.input.{name}.range: min is greater than max"
            if item.type == "text" and item.enum is None and item.pattern is None:
                yield f"actions.{key}.input.{name}: text inputs need an enum or a pattern"
            if item.type == "text" and item.pattern is not None:
                try:
                    re.compile(item.pattern)
                except re.error as exc:
                    yield f"actions.{key}.input.{name}.pattern: {exc}"
            if item.default is not None:
                kind = "actor" if item.type == "actor" else item.type
                if _kind_of(item.default) != ("text" if kind == "actor" else kind):
                    yield f"actions.{key}.input.{name}.default: must be a {item.type}"
                elif item.type == "actor" and not item.default:
                    yield f"actions.{key}.input.{name}.default: an actor id cannot be empty"
                elif item.type == "text":
                    if item.enum is not None and item.default not in item.enum:
                        choices = ", ".join(item.enum)
                        yield f"actions.{key}.input.{name}.default: not one of {choices}"
                    if item.pattern is not None and not re.fullmatch(item.pattern, item.default):
                        yield f"actions.{key}.input.{name}.default: does not match {item.pattern}"
                elif item.type == "integer" and item.range is not None:
                    if not item.range[0] <= item.default <= item.range[1]:
                        yield f"actions.{key}.input.{name}.default: outside the range"
        if action.role == "attack" and not has_target:
            yield f"actions.{key}.input: an attack needs a target_id input of type actor"
        if action.on_hit is not None and action.role != "attack":
            yield f"actions.{key}.on_hit: only attack-role actions may deal damage"
        if action.on_hit is not None and not has_target:
            yield f"actions.{key}.on_hit: needs a target_id input of type actor"
        allowed = {"actor", "mod", "input"} | ({"target", "target_mod"} if has_target else set())
        yield from _check_formula(f"actions.{key}.modifier", action.modifier, allowed)
        yield from _check_formula(f"actions.{key}.target", action.target, allowed)
        for index, requirement in enumerate(action.requires):
            location = f"actions.{key}.requires[{index}]"
            if (requirement.when is None) == (requirement.unique is None):
                yield f"{location}: give exactly one of when or unique"
            if requirement.when is not None:
                yield from _check_formula(f"{location}.when", requirement.when, allowed)
            if requirement.unique is not None:
                rule = requirement.unique
                record = spec.records.get(rule.record)
                if record is None:
                    yield f"{location}.unique.record: unknown record {rule.record!r}"
                elif rule.field not in record.fields:
                    yield f"{location}.unique.field: {rule.field!r} is not a field of {rule.record}"
                elif rule.field not in record.unique:
                    yield (
                        f"{location}.unique.field: {rule.field!r} is not declared in "
                        f"records.{rule.record}.unique"
                    )
                yield from _check_formula(f"{location}.unique.value", rule.value, allowed)
        produced = {rule.outcome for rule in spec.outcomes.get(action.outcomes, [])}
        for outcome, effects in action.on_outcome.items():
            location = f"actions.{key}.on_outcome.{outcome}"
            if outcome not in OUTCOMES:
                yield f"{location}: {outcome!r} is not one of {', '.join(OUTCOMES)}"
            elif outcome not in produced:
                yield (
                    f"{location}: the {action.outcomes!r} table never produces {outcome!r}; "
                    f"it produces {', '.join(sorted(produced)) or 'nothing'}"
                )
            targets = [effect.record for effect in effects]
            for duplicate in sorted({name for name in targets if targets.count(name) > 1}):
                yield f"{location}: writes {duplicate} twice; records are keyed by event id"
            for index, effect in enumerate(effects):
                record = spec.records.get(effect.record)
                if record is None:
                    yield f"{location}[{index}].record: unknown record {effect.record!r}"
                    continue
                missing = sorted(set(record.fields) - set(effect.fields))
                extra = sorted(set(effect.fields) - set(record.fields))
                if missing:
                    yield f"{location}[{index}].fields: missing {', '.join(missing)}"
                if extra:
                    yield f"{location}[{index}].fields: {', '.join(extra)} not in {effect.record}"
                for name, formula in effect.fields.items():
                    yield from _check_formula(
                        f"{location}[{index}].fields.{name}", formula, allowed
                    )
        if action.on_hit is not None:
            yield from _check_formula(f"actions.{key}.on_hit.dice", action.on_hit.dice, allowed)
            yield from _check_formula(f"actions.{key}.on_hit.bonus", action.on_hit.bonus, allowed)
    builtin_actor = {"hp", "conditions", "inventory"}
    for section, allowed in (
        ("actor", builtin_actor | set(spec.stats)),
        ("records", set(spec.records)),
        ("world", {"time"}),
    ):
        entries = getattr(spec.display, section)
        for entry in entries:
            if entry not in allowed:
                yield (
                    f"display.{section}: {entry!r} is not available; choose from "
                    f"{', '.join(sorted(allowed)) or 'nothing'}"
                )
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in entries:
            (duplicates if entry in seen else seen).add(entry)
        for duplicate in sorted(duplicates):
            yield f"display.{section}: {duplicate!r} listed twice"
    if spec.example_world is not None:
        for actor_id, actor in spec.example_world.actors.items():
            if not re.fullmatch(r"^[a-z0-9][a-z0-9_.-]{0,60}$", actor_id):
                yield (
                    f"example_world.actors.{actor_id}: actor ids are lower-case identifiers "
                    "so commands can address them"
                )
            for condition in actor.conditions:
                if condition not in spec.conditions:
                    yield (
                        f"example_world.actors.{actor_id}.conditions: "
                        f"unknown condition {condition!r}"
                    )
            if not 0 <= actor.hp <= actor.max_hp or actor.max_hp <= 0:
                yield f"example_world.actors.{actor_id}: hp must be within 0..max_hp"


_RESERVED_NAMES = frozenset(
    {"schema", "dict", "json", "copy", "validate", "construct", "fields", "parse_obj"}
)


def _reserved_name(name: str) -> bool:
    # Payload models are pydantic classes; these names collide with its own attributes.
    return name.startswith("model_") or name in _RESERVED_NAMES


def _check_typed(location: str, field: RecordField):
    if field.type == "integer":
        if field.range is None:
            yield f"{location}.range: integer fields need a [min, max] range"
        elif field.range[0] > field.range[1]:
            yield f"{location}.range: min is greater than max"
    elif field.range is not None:
        yield f"{location}: range applies to integer fields only"
    if field.type == "text":
        if field.enum is None and field.pattern is None:
            yield f"{location}: text fields need an enum or a pattern"
        if field.pattern is not None:
            try:
                re.compile(field.pattern)
            except re.error as exc:
                yield f"{location}.pattern: {exc}"
    elif field.enum is not None or field.pattern is not None:
        yield f"{location}: enum and pattern apply to text fields only"


def _check_default(location: str, stat: StatSpec):
    value = stat.default
    if stat.type == "integer" and stat.range is not None:
        if not stat.range[0] <= value <= stat.range[1]:
            yield f"{location}: outside the range {stat.range[0]}..{stat.range[1]}"
    elif stat.type == "text":
        if stat.enum is not None and value not in stat.enum:
            yield f"{location}: not one of {', '.join(stat.enum)}"
        if stat.pattern is not None:
            try:
                if not re.fullmatch(stat.pattern, value):
                    yield f"{location}: does not match {stat.pattern}"
            except re.error:
                pass


def _kind_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    return "text"
