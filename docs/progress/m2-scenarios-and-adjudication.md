# M2: Scenario Cards, Grounded NL Intent, and Free-Action Adjudication (A1)

Date: 2026-11-27 (Asia/Seoul).
Status: implemented and verified offline on branch `Sallos725/m2-scenarios-nl-and-adjudication`.
Direction: [DIRECTION.md](../DIRECTION.md). Contract: [ADR 0011](../adr/0011-scenarios-and-free-action-adjudication.md).

## Completed

### Slice 1 — Scenario card initial-world contract
- Added `saga_trpg/rulebooks/scenarios.py` (`ScenarioSpec` v1, `parse_scenario`,
  `load_scenario`, `scenario_problems`, `build_scenario_world`, `build_scenario_module`),
  separating initial world data (actors, NPCs, hand-authored or seeded map, flags,
  `quest_state`, setting narration) from rulebook mechanics.
- Extended `CreateWorldRequest` (`POST /worlds/{world_id}`) and protocol v4 `HostTurn`
  (`POST /v1/saga/turn`, `POST /v1/chat/completions`) to accept an optional scenario JSON
  when creating a new world; existing worlds keep their pinned genesis state unchanged.
- Added CLI subcommands `python -m saga_trpg.rulebooks check-scenario` and
  `scenario-module`, plus example scenario `packs/scenarios/black-tower-outpost/scenario.json`.

### Slice 2 — Grounded natural-language intent across rulebooks (including `move`)
- Added `saga_trpg/adapters/intent/rulebook_grounding.py`:
  - `intent_context_for`: builds map-leak-free intent arguments exposing only the player's
    current room exits (with Korean/English direction hints) and actor roster (ADR 0009).
  - `ground_proposal` / `ground_proposal_or_narrative`: strictly validates proposed actions
    against the rulebook's closed payload models, current room exits for `map_move`, and
    committed actors for `target_id`.
- Updated `OpenAICompatIntentTool` (`risu-openai-intent-v2` tape key) and `POST /v1/saga/turn`
  `client_proposal` validation to use grounded rulebook + `adjudicate_action` + `narrative_turn`
  validation before any world mutation.

### Slice 3 — Free-action GM adjudication (A1 first slice)
- Added reserved runtime event `adjudicate_action` (`AdjudicateActionPayload`, closed schema:
  `mode` in `auto_success | check | impossible`, `difficulty` 1..100, `modifier` -20..20,
  `advantage`/`disadvantage` on `1d20`, allow-listed `dice_expression` `1d20 | 2d6 | 4d3-8`,
  `ability`, `stakes`, `reason`).
- Handled deterministically in `RuntimeEngine` via `DicePort`, producing zero direct state
  patches in Slice 1 and immutable journaled resolution so regeneration can never alter
  difficulty or re-roll outcomes.

## Verification & Self-Review

- Full offline verification: `pytest` (638 passed), `node --test tests/risu_provider.test.cjs`,
  `ruff check`, `ruff format --check`, `compileall`, and `scripts/export_rulesets.py --check`.
- `CLAUDE.md` 5-point invariant check:
  1. Dice/tools twice on rerender: **No** — committed journal entries return before
     `client_proposal`, tape intent, or `DicePort` run.
  2. Narration mutating state: **No** — `adjudicate_action` commits through `RuntimeEngine`
     with zero direct HP/inventory/map mutation; narration remains read-only.
  3. Wall-clock in replay: **No** — seeded scenario maps and `adjudicate_action` rolls replay
     identically from genesis.
  4. NMOS duplication: **No** — scenario state stays in `WorldState`/journal.
  5. Benchmark comparability: **No** — `adjudicate_action` is not in `command_verbs` and
     existing rulebook bundles/scenarios are untouched.

## Next

- M3: authoritative roll/result cards and active scene participant board (B1·B2), map v2
  (inspection checks, locked exits, loot, NPC movement), quest tracking, and explicit GM
  correction (A3).
