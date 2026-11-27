# M3: Exploration Depth, Quests, GM Corrections (A3), and Turn/Scene Cards (B1·B2)

Date: 2026-11-27 (Asia/Seoul).
Status: implemented and verified offline on branch `Sallos725/m3-exploration-gm-and-ui`.
Direction: [DIRECTION.md](../DIRECTION.md). Contract: [ADR 0012](../adr/0012-exploration-gm-correction-and-cards.md).

## Completed

- **Map v2 & Quests**: hash-neutral `Exit.locked`/`key_item` and `Room.loot`/`clues`/
  `discovered_clues`; locked-exit guard in `DataRules._move`; reserved deterministic events
  `unlock_exit`, `take_loot`, `inspect_room`, `move_npc`, and `quest_update`; enriched
  `packs/scenarios/black-tower-outpost/scenario.json`.
- **A3 GM Correction**: append-only `gm_correction` event (`reason`, optional
  `target_event_id`, clamped HP deltas, non-negative inventory deltas, conditions, quests,
  flags) with full `StatePatch` audit trail and untouched prior journal entries.
- **B1 & B2 Projections + Plugin v0.9.0**: `CommittedTurn.turn_card()` and
  `CommittedTurn.scene_board()` (epistemic room filtering); plugin v0.9.0 forwards
  `saga:scenario:` entries, renders B1 outcome/change deltas in the footer, and displays
  B2 Scene Board + B1 Turn Card in the fullscreen SAGA panel.

## Verification

- `pytest`: 641 passed (including `tests/test_m3_exploration_gm.py`)
- `node --test tests/risu_provider.test.cjs`: 36 passed
- `ruff check`, `ruff format --check`, `compileall`, `scripts/export_rulesets.py --check`: clean.
