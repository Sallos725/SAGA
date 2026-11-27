# ADR 0012: Map v2 Exploration State, Quests, Append-Only GM Correction (A3), and Turn/Scene Cards (B1·B2)

Date: 2026-11-27
Status: **Accepted (M3)**

## Context

M1 and M2 established the RisuAI Hook/Provider foundation, scenario cards, grounded NL
intent, and A1 free-action adjudication. To support sustained dungeon/frontier campaigns
without losing state consistency across revisits or GM fixes, SAGA needs:

1. Persistent room-level exploration state (locked exits, finite loot, discoverable clues,
   NPC room movement) and quest progression while preserving hash neutrality for existing
   map-less and v1-map worlds ([ADR 0009](0009-locations-and-map-movement.md)).
2. Explicit, auditable GM corrections and scene settlement (A3) that never rewrite past
   journal history.
3. Read-only projections for roll/change cards (B1) and active scene participant boards
   (B2) that respect epistemic room visibility.

## Decisions

### 1. Hash-neutral Map v2 fields and deterministic exploration events

- `Exit` adds optional `locked: bool = False` (`exclude_if=_false`) and `key_item: str | None`
  (`exclude_if=_absent`). `Room` adds optional `loot`, `clues`, and `discovered_clues`
  (`exclude_if=_empty`). Pre-M3 maps serialize byte-for-byte identically.
- `DataRules._move` refuses movement through locked exits.
- `known_map` hides destinations behind locked or closed-door exits, surfaces `room.loot`,
  and exposes only `discovered_clues` (never undiscovered `room.clues`).
- Five reserved runtime events execute deterministically with full `StatePatch`es:
  `unlock_exit`, `take_loot`, `inspect_room`, `move_npc`, and `quest_update`.
- Finite loot is atomically removed from `room.loot` and added to `actor.inventory`,
  preventing duplicate claims across revisits or regenerations.

### 2. Explicit append-only GM correction (`gm_correction`, A3)

- `gm_correction` (`GmCorrectionPayload`) requires a non-empty `reason` and at least one
  validated delta (`hp_changes`, `inventory_changes`, `add_conditions`, `remove_conditions`,
  `quest_updates`, `flags_set`), plus optional `target_event_id` provenance.
- Historical journal entries are never mutated in place; the correction appends a new
  committed event with exact before→after `StatePatch`es.

### 3. Read-only B1 `turn_card` and B2 `scene_board` projections

- Protocol v4 responses (`POST /v1/saga/turn` and v4 `POST /v1/chat/completions`) include:
  - `turn_card` (B1): immutable snapshot of the turn's `event_id`, `seq`, `kind`, `rolls`,
    `resolution`, `changes` (from committed patches), and `facts`.
  - `scene_board` (B2): current room name, exits (with `locked`), `room_loot`,
    `discovered_clues`, visible `participants` (current room only when mapped—never actors
    in unseen rooms), and `quests`.
- Plugin v0.9.0 forwards `saga:scenario:` lorebook entries, renders B1 outcome/deltas in
  the escaped footer, and displays B2 Scene Board + latest B1 Turn Card in the SAGA panel.
