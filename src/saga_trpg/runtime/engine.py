from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

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
    CommittedEvent,
    GameEvent,
    GmCorrectionPayload,
    InspectRoomPayload,
    MoveNpcPayload,
    NarrativeTurnPayload,
    QuestUpdatePayload,
    TakeLootPayload,
    UnlockExitPayload,
)
from saga_trpg.domain.models import RuleResolution, RuleResult, StatePatch, WorldState
from saga_trpg.modules.base import RuleModule
from saga_trpg.ports.dice import DicePort
from saga_trpg.runtime.hashing import stable_model_hash
from saga_trpg.runtime.journal import JournalEntry, JournalPort
from saga_trpg.runtime.replay import replay_recorded_results


class IdempotencyConflict(ValueError):
    """A stable identity was reused for a different event proposal."""


def validate_event_retry(proposed: GameEvent, saved: GameEvent) -> None:
    identity = {"event_id": None, "idempotency_key": None}
    same_action = stable_model_hash(proposed.model_copy(update=identity)) == stable_model_hash(
        saved.model_copy(update=identity)
    )
    same_id = proposed.event_id is None or proposed.event_id == saved.event_id
    same_key = proposed.idempotency_key is None or proposed.idempotency_key == saved.idempotency_key
    if not (same_action and same_id and same_key):
        raise IdempotencyConflict("idempotency conflict: identity reused for another action")


@dataclass
class DispatchOutcome:
    entry: JournalEntry
    replayed: bool = False


class RuntimeEngine:
    def __init__(
        self,
        *,
        initial_state: WorldState,
        rules: RuleModule,
        dice: DicePort,
        journal: JournalPort,
        event_id_prefix: str | None = None,
    ) -> None:
        self.state = initial_state.model_copy(deep=True)
        self.rules = rules
        self.dice = dice
        self.journal = journal
        self.event_id_prefix = event_id_prefix or initial_state.world_id

    def dispatch(self, proposed: GameEvent) -> DispatchOutcome:
        event = GameEvent.model_validate(proposed.model_dump())
        by_id = self.journal.find_by_event_id(event.event_id) if event.event_id else None
        by_key = (
            self.journal.find_by_idempotency_key(event.idempotency_key)
            if event.idempotency_key
            else None
        )
        if by_id is not None and by_key is not None and by_id != by_key:
            raise IdempotencyConflict("idempotency conflict: identities refer to different events")
        existing = by_id or by_key
        if existing is not None:
            validate_event_retry(event, existing.committed.event)
            return DispatchOutcome(entry=existing, replayed=True)

        next_seq = self.state.seq + 1
        event.event_id = event.event_id or f"{self.event_id_prefix}:e{next_seq}"

        existing = self.journal.find_by_event_id(event.event_id)
        if existing:
            raise IdempotencyConflict("idempotency conflict: generated event id already exists")

        before_hash = stable_model_hash(self.state)
        working = self.state.model_copy(deep=True)
        if event.kind == NARRATIVE_TURN_KIND:
            NarrativeTurnPayload.model_validate(event.payload)
            if event.actor_id not in working.actors:
                raise ValueError(f"unknown actor: {event.actor_id}")
            result = RuleResult(
                state=working,
                facts=[f"{event.actor_id} takes a narrative turn"],
                rolls=[],
                patches=[],
                trace=[f"runtime:{NARRATIVE_TURN_KIND} actor={event.actor_id}"],
                resolution=None,
            )
        elif event.kind == ADJUDICATE_ACTION_KIND:
            result = self._apply_adjudicate_action(working, event)
        elif event.kind == UNLOCK_EXIT_KIND:
            result = self._apply_unlock_exit(working, event)
        elif event.kind == TAKE_LOOT_KIND:
            result = self._apply_take_loot(working, event)
        elif event.kind == INSPECT_ROOM_KIND:
            result = self._apply_inspect_room(working, event)
        elif event.kind == MOVE_NPC_KIND:
            result = self._apply_move_npc(working, event)
        elif event.kind == QUEST_UPDATE_KIND:
            result = self._apply_quest_update(working, event)
        elif event.kind == GM_CORRECTION_KIND:
            result = self._apply_gm_correction(working, event)
        else:
            result = self.rules.apply(state=working, event=event, dice=self.dice)
        result.state.seq = next_seq
        after_hash = stable_model_hash(result.state)

        committed = CommittedEvent(
            event=event,
            seq=next_seq,
            state_hash_before=before_hash,
            state_hash_after=after_hash,
        )
        entry = JournalEntry(committed=committed, result=result)
        replay_recorded_results(self.state, [entry])

        # Journal before exposing the new in-memory state. Persistent JournalPort implementations
        # should fail atomically if they cannot record the event.
        self.journal.append(entry)
        self.state = result.state.model_copy(deep=True)
        return DispatchOutcome(entry=entry, replayed=False)

    def _apply_adjudicate_action(self, working: WorldState, event: GameEvent) -> RuleResult:
        payload = AdjudicateActionPayload.model_validate(event.payload)
        if event.actor_id not in working.actors:
            raise ValueError(f"unknown actor: {event.actor_id}")
        assert event.event_id is not None
        if payload.mode == "impossible":
            reason = f": {payload.reason}" if payload.reason else ""
            return RuleResult(
                state=working,
                facts=[f"{event.actor_id} cannot perform '{payload.description}'{reason}"],
                rolls=[],
                patches=[],
                trace=[f"runtime:{ADJUDICATE_ACTION_KIND} mode=impossible actor={event.actor_id}"],
                resolution=None,
            )
        if payload.mode == "auto_success":
            reason = f" ({payload.reason})" if payload.reason else ""
            facts = [f"{event.actor_id} succeeds without a roll: {payload.description}{reason}"]
            if payload.stakes:
                facts.append(f"Stakes: {payload.stakes}")
            return RuleResult(
                state=working,
                facts=facts,
                rolls=[],
                patches=[],
                trace=[
                    f"runtime:{ADJUDICATE_ACTION_KIND} mode=auto_success actor={event.actor_id}"
                ],
                resolution=None,
            )
        assert payload.difficulty is not None
        if payload.dice_expression == "1d20" and payload.advantage != payload.disadvantage:
            r0 = self.dice.roll(
                world_id=working.world_id,
                event_id=event.event_id,
                roll_key="adjudicate:0",
                expression="1d20",
            )
            r1 = self.dice.roll(
                world_id=working.world_id,
                event_id=event.event_id,
                roll_key="adjudicate:1",
                expression="1d20",
            )
            chosen = (
                r0 if (r0.total >= r1.total if payload.advantage else r0.total <= r1.total) else r1
            )
            rolls = [r0, r1]
        else:
            r0 = self.dice.roll(
                world_id=working.world_id,
                event_id=event.event_id,
                roll_key="adjudicate:0",
                expression=payload.dice_expression,
            )
            chosen = r0
            rolls = [r0]
        total = chosen.total + payload.modifier
        outcome = "success" if total >= payload.difficulty else "failure"
        ability_note = f" [{payload.ability}]" if payload.ability else ""
        facts = [
            f"{event.actor_id} attempts '{payload.description}'{ability_note}: "
            f"{outcome} ({total} vs DC {payload.difficulty})"
        ]
        if payload.stakes:
            facts.append(f"Stakes: {payload.stakes}")
        if payload.reason:
            facts.append(f"Basis: {payload.reason}")
        return RuleResult(
            state=working,
            facts=facts,
            rolls=rolls,
            patches=[],
            trace=[
                f"runtime:{ADJUDICATE_ACTION_KIND} mode=check actor={event.actor_id} "
                f"expr={payload.dice_expression} mod={payload.modifier:+d} "
                f"total={total} target={payload.difficulty} outcome={outcome}"
            ],
            resolution=RuleResolution(
                outcome=outcome,
                total=total,
                target=payload.difficulty,
                pending_adjudication=False,
            ),
        )

    def _apply_unlock_exit(self, working: WorldState, event: GameEvent) -> RuleResult:
        payload = UnlockExitPayload.model_validate(event.payload)
        if working.map is None:
            raise ValueError("this world has no map")
        actor = working.actors.get(event.actor_id)
        if actor is None:
            raise ValueError(f"unknown actor: {event.actor_id}")
        if actor.location is None:
            raise ValueError(f"{event.actor_id} is not on the map")
        room = working.map.rooms[actor.location]
        exit_ = room.exits.get(payload.exit)
        if exit_ is None:
            raise ValueError(f"{room.name} has no exit {payload.exit!r}")
        if not exit_.locked:
            raise ValueError(f"{exit_.label} ({payload.exit}) is already unlocked")
        if exit_.key_item and actor.inventory.get(exit_.key_item, 0) < 1:
            raise ValueError(f"unlocking {exit_.label} requires {exit_.key_item}")
        exit_.locked = False
        used = f" using {exit_.key_item}" if exit_.key_item else ""
        return RuleResult(
            state=working,
            facts=[f"{event.actor_id} unlocks {exit_.label} ({payload.exit}){used}"],
            rolls=[],
            patches=[
                StatePatch(
                    path=f"map.rooms.{actor.location}.exits.{payload.exit}.locked",
                    before=True,
                    after=False,
                )
            ],
            trace=[
                f"runtime:{UNLOCK_EXIT_KIND} actor={event.actor_id} "
                f"room={actor.location} exit={payload.exit}"
            ],
            resolution=None,
        )

    def _apply_take_loot(self, working: WorldState, event: GameEvent) -> RuleResult:
        payload = TakeLootPayload.model_validate(event.payload)
        if working.map is None:
            raise ValueError("this world has no map")
        actor = working.actors.get(event.actor_id)
        if actor is None:
            raise ValueError(f"unknown actor: {event.actor_id}")
        if actor.location is None:
            raise ValueError(f"{event.actor_id} is not on the map")
        room = working.map.rooms[actor.location]
        available = room.loot.get(payload.item, 0)
        if available < payload.amount:
            raise ValueError(
                f"{room.name} does not have {payload.amount}× {payload.item} "
                f"(available: {available})"
            )
        new_room_count = available - payload.amount
        if new_room_count == 0:
            del room.loot[payload.item]
        else:
            room.loot[payload.item] = new_room_count
        before_inv = actor.inventory.get(payload.item, 0)
        after_inv = before_inv + payload.amount
        actor.inventory[payload.item] = after_inv
        return RuleResult(
            state=working,
            facts=[f"{event.actor_id} takes {payload.amount}× {payload.item} from {room.name}"],
            rolls=[],
            patches=[
                StatePatch(
                    path=f"map.rooms.{actor.location}.loot.{payload.item}",
                    before=available,
                    after=new_room_count,
                ),
                StatePatch(
                    path=f"actors.{event.actor_id}.inventory.{payload.item}",
                    before=before_inv,
                    after=after_inv,
                ),
            ],
            trace=[
                f"runtime:{TAKE_LOOT_KIND} actor={event.actor_id} "
                f"room={actor.location} item={payload.item} amount={payload.amount}"
            ],
            resolution=None,
        )

    def _apply_inspect_room(self, working: WorldState, event: GameEvent) -> RuleResult:
        payload = InspectRoomPayload.model_validate(event.payload)
        if working.map is None:
            raise ValueError("this world has no map")
        actor = working.actors.get(event.actor_id)
        if actor is None:
            raise ValueError(f"unknown actor: {event.actor_id}")
        if actor.location is None:
            raise ValueError(f"{event.actor_id} is not on the map")
        room = working.map.rooms[actor.location]
        before = list(room.discovered_clues)
        discovered_set = set(before)
        if payload.clue_id is not None:
            if payload.clue_id not in room.clues:
                raise ValueError(f"{room.name} has no clue {payload.clue_id!r}")
            targets = [payload.clue_id] if payload.clue_id not in discovered_set else []
        else:
            targets = [k for k in sorted(room.clues) if k not in discovered_set]
        patches: list[StatePatch] = []
        if targets:
            room.discovered_clues = sorted(discovered_set | set(targets))
            patches.append(
                StatePatch(
                    path=f"map.rooms.{actor.location}.discovered_clues",
                    before=before,
                    after=list(room.discovered_clues),
                )
            )
            facts = [
                f"{event.actor_id} discovers clue '{cid}' in {room.name}: {room.clues[cid]}"
                for cid in targets
            ]
        else:
            facts = [f"{event.actor_id} inspects {room.name} and finds no new clues"]
        return RuleResult(
            state=working,
            facts=facts,
            rolls=[],
            patches=patches,
            trace=[
                f"runtime:{INSPECT_ROOM_KIND} actor={event.actor_id} "
                f"room={actor.location} found={len(targets)}"
            ],
            resolution=None,
        )

    def _apply_move_npc(self, working: WorldState, event: GameEvent) -> RuleResult:
        payload = MoveNpcPayload.model_validate(event.payload)
        if working.map is None:
            raise ValueError("this world has no map")
        if event.actor_id not in working.actors:
            raise ValueError(f"unknown actor: {event.actor_id}")
        npc = working.actors.get(payload.npc_id)
        if npc is None:
            raise ValueError(f"unknown npc: {payload.npc_id}")
        if npc.location is None:
            raise ValueError(f"{payload.npc_id} is not on the map")
        room = working.map.rooms[npc.location]
        exit_ = room.exits.get(payload.exit)
        if exit_ is None:
            raise ValueError(f"{room.name} has no exit {payload.exit!r}")
        if exit_.locked:
            raise ValueError(f"{exit_.label} ({payload.exit}) is locked")
        before_loc = npc.location
        before_visited = sorted(npc.visited_rooms)
        npc.location = exit_.to_room
        npc.visited_rooms.add(exit_.to_room)
        dest = working.map.rooms[exit_.to_room]
        return RuleResult(
            state=working,
            facts=[f"{payload.npc_id} moves through {exit_.label} from {room.name} to {dest.name}"],
            rolls=[],
            patches=[
                StatePatch(
                    path=f"actors.{payload.npc_id}.location",
                    before=before_loc,
                    after=exit_.to_room,
                ),
                StatePatch(
                    path=f"actors.{payload.npc_id}.visited_rooms",
                    before=before_visited,
                    after=sorted(npc.visited_rooms),
                ),
            ],
            trace=[f"runtime:{MOVE_NPC_KIND} npc={payload.npc_id} {before_loc}->{exit_.to_room}"],
            resolution=None,
        )

    def _apply_quest_update(self, working: WorldState, event: GameEvent) -> RuleResult:
        payload = QuestUpdatePayload.model_validate(event.payload)
        if event.actor_id not in working.actors:
            raise ValueError(f"unknown actor: {event.actor_id}")
        before = deepcopy(working.quest_state.get(payload.quest_id))
        current = dict(before) if isinstance(before, dict) else {}
        if payload.stage is not None:
            current["stage"] = payload.stage
        if payload.status is not None:
            current["status"] = payload.status
        if payload.objective is not None:
            current["objective"] = payload.objective
        if payload.note is not None:
            notes = list(current.get("notes", [])) if isinstance(current.get("notes"), list) else []
            notes.append(payload.note)
            current["notes"] = notes
        working.quest_state[payload.quest_id] = current
        summary = ", ".join(
            f"{k}={v}"
            for k, v in (
                ("stage", payload.stage),
                ("status", payload.status),
                ("objective", payload.objective),
            )
            if v is not None
        )
        return RuleResult(
            state=working,
            facts=[f"Quest '{payload.quest_id}' updated ({summary or 'note added'})"],
            rolls=[],
            patches=[
                StatePatch(
                    path=f"quest_state.{payload.quest_id}",
                    before=before,
                    after=deepcopy(current),
                )
            ],
            trace=[f"runtime:{QUEST_UPDATE_KIND} quest={payload.quest_id}"],
            resolution=None,
        )

    def _apply_gm_correction(self, working: WorldState, event: GameEvent) -> RuleResult:
        payload = GmCorrectionPayload.model_validate(event.payload)
        if event.actor_id not in working.actors:
            raise ValueError(f"unknown actor: {event.actor_id}")
        item_re = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
        patches: list[StatePatch] = []
        for aid, delta in sorted(payload.hp_changes.items()):
            actor = working.actors.get(aid)
            if actor is None:
                raise ValueError(f"unknown actor in hp_changes: {aid}")
            before_hp = actor.hp
            after_hp = max(0, min(actor.max_hp, before_hp + delta))
            actor.hp = after_hp
            patches.append(StatePatch(path=f"actors.{aid}.hp", before=before_hp, after=after_hp))
        for aid, items in sorted(payload.inventory_changes.items()):
            actor = working.actors.get(aid)
            if actor is None:
                raise ValueError(f"unknown actor in inventory_changes: {aid}")
            for item_key, delta in sorted(items.items()):
                if not item_re.fullmatch(item_key):
                    raise ValueError(f"invalid item key in gm_correction: {item_key!r}")
                before_cnt = actor.inventory.get(item_key, 0)
                after_cnt = before_cnt + delta
                if after_cnt < 0:
                    raise ValueError(
                        f"gm_correction would make {aid}.inventory.{item_key} negative"
                    )
                if after_cnt == 0:
                    actor.inventory.pop(item_key, None)
                else:
                    actor.inventory[item_key] = after_cnt
                patches.append(
                    StatePatch(
                        path=f"actors.{aid}.inventory.{item_key}",
                        before=before_cnt,
                        after=after_cnt,
                    )
                )
        for aid, conds in sorted(payload.add_conditions.items()):
            actor = working.actors.get(aid)
            if actor is None:
                raise ValueError(f"unknown actor in add_conditions: {aid}")
            before_c = sorted(actor.conditions)
            for cond in conds:
                if not item_re.fullmatch(cond):
                    raise ValueError(f"invalid condition key: {cond!r}")
                actor.conditions.add(cond)
            patches.append(
                StatePatch(
                    path=f"actors.{aid}.conditions",
                    before=before_c,
                    after=sorted(actor.conditions),
                )
            )
        for aid, conds in sorted(payload.remove_conditions.items()):
            actor = working.actors.get(aid)
            if actor is None:
                raise ValueError(f"unknown actor in remove_conditions: {aid}")
            before_c = sorted(actor.conditions)
            for cond in conds:
                actor.conditions.discard(cond)
            patches.append(
                StatePatch(
                    path=f"actors.{aid}.conditions",
                    before=before_c,
                    after=sorted(actor.conditions),
                )
            )
        for qid, qdata in sorted(payload.quest_updates.items()):
            before_q = deepcopy(working.quest_state.get(qid))
            merged = dict(before_q) if isinstance(before_q, dict) else {}
            merged.update(deepcopy(qdata))
            working.quest_state[qid] = merged
            patches.append(
                StatePatch(path=f"quest_state.{qid}", before=before_q, after=deepcopy(merged))
            )
        for flag_key, flag_val in sorted(payload.flags_set.items()):
            before_f = deepcopy(working.flags.get(flag_key))
            working.flags[flag_key] = deepcopy(flag_val)
            patches.append(
                StatePatch(path=f"flags.{flag_key}", before=before_f, after=deepcopy(flag_val))
            )
        ref = f" (ref {payload.target_event_id})" if payload.target_event_id else ""
        return RuleResult(
            state=working,
            facts=[f"GM correction{ref}: {payload.reason}"],
            rolls=[],
            patches=patches,
            trace=[
                f"runtime:{GM_CORRECTION_KIND} actor={event.actor_id} "
                f"patches={len(patches)}"
                + (f" target_event={payload.target_event_id}" if payload.target_event_id else "")
            ],
            resolution=None,
        )

    def get_event(self, event_id: str) -> JournalEntry | None:
        return self.journal.find_by_event_id(event_id)
