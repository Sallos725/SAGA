"""Render a rulebook's `display` declaration against a world state.

The result is plain strings the host footer prints as is, so the host never learns what a
stat or a record means. Nothing here is game state; it is a read-only projection.
"""

from __future__ import annotations

from typing import Any

from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.rulebooks.loader import RulebookSpec

_UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))


def format_time(seconds: int) -> str:
    parts = []
    rest = max(0, seconds)
    for label, size in _UNITS:
        count, rest = divmod(rest, size)
        if count or (label == "s" and not parts):
            parts.append(f"{count}{label}")
    return " ".join(parts)


def _signed(value: Any) -> str:
    return f"{value:+d}" if isinstance(value, int) and not isinstance(value, bool) else str(value)


def _actor_item(spec: RulebookSpec, actor: ActorState, entry: str) -> str | None:
    if entry == "hp":
        return f"{actor.hp}/{actor.max_hp}"
    if entry == "conditions":
        return ", ".join(sorted(actor.conditions)) or None
    if entry == "inventory":
        items = ", ".join(f"{key}×{count}" for key, count in sorted(actor.inventory.items()))
        return f"🎒 {items}" if items else None
    if entry.endswith(":*"):
        prefix = entry[:-2] + ":"
        family = spec.stats[entry]
        members = sorted(
            (key[len(prefix) :], value)
            for key, value in actor.stats.items()
            if key.startswith(prefix) and value is not False
        )
        if family.type == "boolean":
            return ", ".join(name for name, _ in members) or None
        return ", ".join(f"{name} {_signed(value)}" for name, value in members) or None
    value = actor.stats.get(entry)
    return None if value is None else f"{entry} {value}"


def _record_item(fields: list[str], record: Any) -> str:
    if not isinstance(record, dict):
        return str(record)
    name = record.get("name")
    rest = [f"{key} {record[key]}" for key in fields if key != "name" and key in record]
    if name is None:
        return ", ".join(rest)
    return f"{name} ({', '.join(rest)})" if rest else str(name)


def render_display(spec: RulebookSpec, state: WorldState) -> dict[str, Any]:
    actors = []
    for actor_id, actor in state.actors.items():
        items = [_actor_item(spec, actor, entry) for entry in spec.display.actor]
        actors.append({"id": actor_id, "items": [item for item in items if item]})
    records = []
    for collection in spec.display.records:
        fields = list(spec.records[collection].fields) if collection in spec.records else []
        stored = state.flags.get(collection, {})
        if not isinstance(stored, dict) or not stored:
            continue
        records.append(
            {
                "label": collection,
                "items": [_record_item(fields, record) for record in stored.values()],
            }
        )
    world = []
    if "time" in spec.display.world:
        world.append(f"⏱ {format_time(state.simulated_time)}")
    return {"actors": actors, "records": records, "world": world}
