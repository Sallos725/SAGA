"""Grounded proposal validation and safe intent context across any DataRules rulebook (M2).

Pure helpers with no network I/O or wall-clock reads:
- Builds map-leak-free intent arguments (`intent_context_for`), exposing only the player's
  current room exits (never unseen rooms or other actors' locations, ADR 0009).
- Validates untrusted LLM or auxiliary-model proposals strictly against the rulebook's
  closed payload models, current room exits for `map_move`, roster actors for `target_id`,
  `adjudicate_action`, and `narrative_turn`."""

from __future__ import annotations

from typing import Any

from pydantic import JsonValue

from saga_trpg.domain.events import (
    ADJUDICATE_ACTION_KIND,
    GM_CORRECTION_KIND,
    INSPECT_ROOM_KIND,
    MOVE_NPC_KIND,
    NARRATIVE_TURN_KIND,
    QUEST_UPDATE_KIND,
    TAKE_LOOT_KIND,
    UNLOCK_EXIT_KIND,
    AdjudicateActionPayload,
    GameEvent,
    GmCorrectionPayload,
    InspectRoomPayload,
    MoveNpcPayload,
    NarrativeTurnPayload,
    QuestUpdatePayload,
    TakeLootPayload,
    UnlockExitPayload,
)
from saga_trpg.domain.maps import known_map
from saga_trpg.domain.models import WorldState
from saga_trpg.rulebooks.commands import rules_for
from saga_trpg.rulebooks.loader import BuiltinAction, RulebookSpec

DIRECTION_HINTS: dict[str, list[str]] = {
    "north": ["north", "북", "북쪽"],
    "south": ["south", "남", "남쪽"],
    "east": ["east", "동", "동쪽"],
    "west": ["west", "서", "서쪽"],
    "up": ["up", "위", "위쪽", "올라", "계단 위"],
    "down": ["down", "아래", "아래쪽", "내려", "지하"],
}


def allowed_v4_models(spec: RulebookSpec, *, allow_adjudicate: bool = True) -> dict[str, type]:
    models: dict[str, type] = {
        **rules_for(spec).payload_models,
        NARRATIVE_TURN_KIND: NarrativeTurnPayload,
        UNLOCK_EXIT_KIND: UnlockExitPayload,
        TAKE_LOOT_KIND: TakeLootPayload,
        INSPECT_ROOM_KIND: InspectRoomPayload,
        MOVE_NPC_KIND: MoveNpcPayload,
        QUEST_UPDATE_KIND: QuestUpdatePayload,
        GM_CORRECTION_KIND: GmCorrectionPayload,
    }
    if allow_adjudicate:
        models[ADJUDICATE_ACTION_KIND] = AdjudicateActionPayload
    return models


def intent_context_for(
    *,
    text: str,
    actor_id: str,
    spec: RulebookSpec,
    state: WorldState,
    allow_adjudicate: bool = True,
) -> dict[str, JsonValue]:
    """Safe intent-classifier input: current room exits/loot only, never unseen rooms."""
    view = known_map(state, actor_id)
    exits: list[dict[str, Any]] = []
    if view is not None:
        for exit_ in view.exits:
            exits.append(
                {
                    "key": exit_.key,
                    "label": exit_.label,
                    "locked": exit_.locked,
                    "hints": DIRECTION_HINTS.get(exit_.key, [exit_.key]),
                }
            )
    actions: dict[str, Any] = {
        kind: model.model_json_schema() for kind, model in rules_for(spec).payload_models.items()
    }
    for kind, model in (
        (NARRATIVE_TURN_KIND, NarrativeTurnPayload),
        (UNLOCK_EXIT_KIND, UnlockExitPayload),
        (TAKE_LOOT_KIND, TakeLootPayload),
        (INSPECT_ROOM_KIND, InspectRoomPayload),
        (QUEST_UPDATE_KIND, QuestUpdatePayload),
    ):
        actions[kind] = model.model_json_schema()
    if allow_adjudicate:
        actions[ADJUDICATE_ACTION_KIND] = AdjudicateActionPayload.model_json_schema()
    return {
        "text": text,
        "actor_id": actor_id,
        "ruleset": f"{spec.id}@{spec.version}",
        "actors": sorted(state.actors),
        "current_room": view.room_name if view is not None else None,
        "room_loot": view.loot if view is not None else {},
        "exits": exits,
        "quests": sorted(state.quest_state),
        "actions": actions,
    }


def ground_proposal(
    value: object,
    *,
    spec: RulebookSpec,
    state: WorldState,
    actor_id: str,
    allow_adjudicate: bool = True,
) -> GameEvent:
    """Strictly validate and ground an untrusted proposal against spec + current state."""
    if not isinstance(value, dict):
        raise ValueError("proposal must be a JSON object")
    kind = value.get("kind")
    raw_payload = value.get("payload", {})
    if not isinstance(kind, str) or not isinstance(raw_payload, dict):
        raise ValueError("proposal must contain string 'kind' and object 'payload'")
    models = allowed_v4_models(spec, allow_adjudicate=allow_adjudicate)
    model = models.get(kind)
    if model is None:
        raise ValueError(f"unsupported proposed action {kind!r} for {spec.id}@{spec.version}")
    validated = model.model_validate(raw_payload)
    dumped = validated.model_dump()

    # Ground target_id against the committed actor roster.
    target_id = dumped.get("target_id")
    if isinstance(target_id, str) and target_id not in state.actors:
        raise ValueError(f"unknown target actor {target_id!r}")

    # Ground map_move exits against the player's current room exits (never hallucinate exits).
    action_spec = spec.actions.get(kind)
    if isinstance(action_spec, BuiltinAction) and action_spec.builtin == "map_move":
        view = known_map(state, actor_id)
        if view is None:
            raise ValueError("this world has no map for movement")
        valid_exits = {exit_.key for exit_ in view.exits}
        chosen_exit = dumped.get("exit")
        if chosen_exit not in valid_exits:
            raise ValueError(
                f"no exit {chosen_exit!r} from {view.room_name}; "
                f"available exits: {', '.join(sorted(valid_exits)) or 'none'}"
            )

    return GameEvent(kind=kind, actor_id=actor_id, payload=dumped)


def ground_proposal_or_narrative(
    value: object,
    *,
    spec: RulebookSpec,
    state: WorldState,
    actor_id: str,
    allow_adjudicate: bool = True,
) -> GameEvent:
    """Grounded validation that safely degrades invalid LLM output to narrative_turn."""
    try:
        return ground_proposal(
            value,
            spec=spec,
            state=state,
            actor_id=actor_id,
            allow_adjudicate=allow_adjudicate,
        )
    except ValueError:
        return GameEvent(kind=NARRATIVE_TURN_KIND, actor_id=actor_id, payload={})
