"""Commands derived from a rulebook: `<action> <positional…> [key=value…] [advantage|disadvantage]`.

Positional values fill the action's inputs in declaration order; builtin actions have a fixed
order (target, then amount/condition/item…). Parsing is pure and canonical: case and spacing do
not change the result, so a command's event identity is stable. A few SRD-era verbs stay as
aliases when the rulebook has the action they point to.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from pydantic import ValidationError

from saga_trpg.domain.events import GameEvent
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import BuiltinAction, CheckAction, InputSpec, RulebookSpec

ALIASES = {
    "check": "ability_check",
    "save": "saving_throw",
    "inflict": "apply_condition",
    "cure": "remove_condition",
    "wait": "advance_time",
}
_FLAGS = ("advantage", "disadvantage")
_BUILTIN_ORDER = {
    "hp_change": ["target_id", "amount"],
    "condition_add": ["target_id", "condition"],
    "condition_remove": ["target_id", "condition"],
    "time_advance": ["amount", "unit"],
    "inventory_add": ["target_id", "item", "amount"],
    "inventory_remove": ["target_id", "item", "amount"],
}
_INTEGER = re.compile(r"^-?[0-9]{1,9}$")


class CommandError(ValueError):
    """The text is not a command of this rulebook; the message names the field or reason."""


def _positional(spec: RulebookSpec, kind: str) -> list[str]:
    action = spec.actions[kind]
    if isinstance(action, BuiltinAction):
        return list(_BUILTIN_ORDER[action.builtin])
    return list(action.input)


def _input_kinds(spec: RulebookSpec, kind: str) -> dict[str, Any]:
    """Field name → integer | boolean | word | text | list of enum members.

    `word` values are closed lower-case identifiers (actor ids, conditions, items, units);
    enum members map case-insensitively back to their declared spelling; `text` keeps its
    case. Cosmetic variants of a command therefore share one identity."""
    action = spec.actions[kind]
    if isinstance(action, BuiltinAction):
        kinds: dict[str, Any] = {name: "word" for name in _BUILTIN_ORDER[action.builtin]}
        kinds["amount"] = "integer"
        return kinds
    return {name: _kind_of(item) for name, item in action.input.items()}


def _kind_of(item: InputSpec) -> Any:
    if item.type in ("integer", "boolean"):
        return item.type
    if item.enum is not None:
        return list(item.enum)
    if item.type == "actor":
        return "word"
    return "text"


def _typed(name: str, kind: Any, raw: str) -> Any:
    if isinstance(kind, list):
        for member in kind:
            if member.lower() == raw.lower():
                return member
        raise CommandError(f"{name} must be one of {', '.join(kind)}, got {raw!r}")
    if kind == "word":
        raw = raw.lower()
        return raw.removesuffix("s") if name == "unit" and raw.endswith("s") else raw
    if kind == "integer":
        if not _INTEGER.fullmatch(raw):
            raise CommandError(f"{name} must be a whole number, got {raw!r}")
        return int(raw)
    if kind == "boolean":
        if raw.lower() not in ("true", "false"):
            raise CommandError(f"{name} must be true or false, got {raw!r}")
        return raw.lower() == "true"
    return raw


_RULES: dict[int, tuple[RulebookSpec, DataRules]] = {}


def rules_for(spec: RulebookSpec) -> DataRules:
    """Payload models for a spec, built once per spec object."""
    cached = _RULES.get(id(spec))
    if cached is not None and cached[0] is spec:
        return cached[1]
    rules = DataRules(spec)
    _RULES[id(spec)] = (spec, rules)
    return rules


def command_verbs(spec: RulebookSpec) -> frozenset[str]:
    """Words that start a command of this rulebook, including the aliases it supports."""
    return frozenset(spec.actions) | {
        alias for alias, target in ALIASES.items() if target in spec.actions
    }


def parse_command(spec: RulebookSpec, *, actor_id: str, text: str) -> GameEvent:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise CommandError(f"unbalanced quote in command: {exc}") from None
    if not tokens:
        raise CommandError("empty command")
    verb = tokens[0].lower()
    kind = verb if verb in spec.actions else ALIASES.get(verb)
    if kind is None or kind not in spec.actions:
        raise CommandError(f"unknown action {verb!r}; see the rulebook's actions")
    action = spec.actions[kind]
    rolls_twice = (
        isinstance(action, CheckAction) and spec.checks[action.check].advantage == "roll_twice"
    )
    order, kinds = _positional(spec, kind), _input_kinds(spec, kind)
    values: dict[str, Any] = {}
    positional: list[str] = []
    for token in tokens[1:]:
        lowered = token.lower()
        if lowered in _FLAGS:
            if not rolls_twice:
                raise CommandError(f"{kind} does not take {lowered}")
            if values.get(lowered):
                raise CommandError(f"{lowered} given twice")
            values[lowered] = True
            continue
        if "=" in token and not token.startswith("="):
            name, raw = token.split("=", 1)
            name = name.lower()
            if name not in kinds:
                raise CommandError(f"{kind} has no input {name!r}")
            if name in values:
                raise CommandError(f"{name} given twice")
            values[name] = _typed(name, kinds[name], raw)
            continue
        positional.append(token)
    free = [name for name in order if name not in values]
    if len(positional) > len(free):
        raise CommandError(f"too many values for {kind}; expected {' '.join(order)}")
    for name, raw in zip(free, positional, strict=False):
        values[name] = _typed(name, kinds[name], raw)
    if rolls_twice:
        values.setdefault("advantage", False)
        values.setdefault("disadvantage", False)
    try:
        payload = rules_for(spec).payload_models[kind].model_validate(values)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "input"
        raise CommandError(f"{kind}: {field}: {first['msg']}") from None
    return GameEvent(kind=kind, actor_id=actor_id, payload=payload.model_dump())


def command_help(spec: RulebookSpec) -> str:
    """One line per action in the positional syntax, for plugins and error messages."""
    lines = []
    reverse = {target: alias for alias, target in ALIASES.items() if target in spec.actions}
    for kind, action in spec.actions.items():
        parts = [kind]
        order = _positional(spec, kind)
        if isinstance(action, BuiltinAction):
            parts += [f"<{name}>" for name in order]
        else:
            for name in order:
                item = action.input[name]
                optional = item.optional or item.default is not None
                parts.append(f"[{name}=…]" if optional else f"<{name}>")
            if spec.checks[action.check].advantage == "roll_twice":
                parts.append("[advantage|disadvantage]")
        line = " ".join(parts)
        if kind in reverse:
            line += f"  (alias: {reverse[kind]})"
        lines.append(line)
    return "\n".join(lines)
