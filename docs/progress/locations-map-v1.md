# Locations and map movement v1

Date: 2026-10-12 (Asia/Seoul). Status: implemented and tested offline; not deployed to 6131 or
6011. Spec: [ADR 0009](../adr/0009-locations-and-map-movement.md). Tracks SAGA-44 (first slice).
Plan: [implementation plan](../superpowers/plans/2026-10-12-locations-map-movement.md).

## Changed

- `WorldState.map` (room graph) and actor `location`/`visited_rooms`, excluded from dumps while
  empty (`Field(exclude_if=...)`; pydantic floor raised to 2.12.4). Map-less worlds keep their
  exact JSON and state hash, and the documented (OpenAPI) schema stays structured.
- `domain/maps.py`: validation, the known-map projection and its text block.
- `domain/mapgen.py`: seeded generator `saga-rooms` v1 (2..30 rooms, spanning tree plus loops,
  opposite-key exits, Korean room table, start room is the entrance hall).
- Rulebooks: `example_world.map` (hand-authored with placements, or `generate`), builtin
  `map_move`, command `move <exit>`, footer entry `location`. Moving is refused for actors with
  a `cannot_act` condition; hand-authored maps are capped at 200 rooms.
- Bundled `srd-5.2.1@0.6.0` (generated map per chat) and `fate-condensed@0.5.0` (harbor map).
  Their narration forbids narrating arrival without a recorded move. New Risu chats now start
  in a generated dungeon; existing chats keep their pinned version and have no map.
- Vertex narration receives the known map as its own part; the committed state it sees has
  `map` and every actor's `location`/`visited_rooms` removed. The footer is rendered for the
  turn's actor.
- Schemas, ruleset packs, `docs/RULEBOOKS.md`, READMEs and integration docs updated.

## Changes from the plan made during review

- Wrap model serializers replaced by `exclude_if`: the serializers collapsed the serialization
  schema used by FastAPI to an untyped object.
- Narrator payload also hides actor positions, not only `map`: an authored placement would have
  leaked an unseen room id and an NPC's whereabouts.
- `cannot_act` guard on `map_move`, the 200-room cap, the narration rule about unrecorded
  movement, and stronger generator and projection tests.

## Verification

- `pytest` 608 passed on Python 3.11, 3.12 and 3.13; `ruff check`, `ruff format --check`,
  `compileall`, `node --test tests/risu_provider.test.cjs` (27 passed) and
  `scripts/export_rulesets.py --check` clean.
- CLAUDE.md checks:
  1. Dice/tools twice on rerender: no. Moves roll nothing; a move replays three times with an
     unchanged journal and no dice calls; the scene and footer are pure reads of the committed
     result.
  2. Narration mutating state: no. The narrator receives a text projection after commit.
  3. Wall-clock in replay: no. The generator is seeded from the rulebook seed and world id, and
     the map is stored at world creation; replay never reruns it.
  4. NMOS duplication: no. The map lives only in `WorldState`; memory receives the existing
     event projection (`player: move (exit north)`).
  5. Benchmark comparability: unchanged. The runner builds worlds from scenario
     `initial_world`, never from `example_world`, and the demo scenario pins `0.5.0`.

## Unresolved

- Natural-language movement ("북쪽으로 간다") needs an interpreter contract change with a tape
  identity design; until then players type `move <exit>` from the footer.
- Benchmark scenarios that spell out a map in `initial_world` are not validated by
  `map_problems`; a dangling exit there would surface as an error at move time.
- No live check yet: the next step is the 6131 test host (backend `saga-runtime-6131`) with a
  real RisuAI client to confirm the footer and narration; 6011 stays untouched until approved.
- Discovery by checks, locked doors, loot, NPC movement, play-time generation and a visual map
  are later slices (ADR 0009, Not in this slice).
