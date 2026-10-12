# Locations and map movement v1

Date: 2026-10-12 (Asia/Seoul). Status: merged (Sallos725/SAGA#31) and deployed to the 6131 test backend
and the 6011 playtest backend on 2026-10-12. Spec: [ADR 0009](../adr/0009-locations-and-map-movement.md). Tracks SAGA-44 (first slice).
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
- Worlds pinned to hand-written SRD 0.1.0–0.3.0 used the latest SRD's command grammar; with
  `move` added in 0.6.0, a committed v3 action such as "Move toward the goblin" would have lost
  its identity and broken the chat. Those worlds now use a frozen 0.5.0 grammar
  (`LEGACY_SRD_SPEC`), pinned by a regression test.

## Verification

- `pytest` 609 passed on Python 3.11, 3.12 and 3.13; `ruff check`, `ruff format --check`,
  `compileall`, `node --test tests/risu_provider.test.cjs` (27 passed) and
  `scripts/export_rulesets.py --check` clean.
- Live, 6131 test backend (`saga-runtime-6131`, `127.0.0.1:4852`, rebuilt from this branch):
  all 12 existing worlds (13 branches, SRD 0.2.0–0.5.0 and Fate 0.2.0/0.4.0) replay unchanged.
  Over HTTP, a new chat (`deploy-check-maps-20261012`, left in place) started on a 9-room
  generated map with the footer `📍 입구 홀 · 출구 north, up, west`; `move north` committed
  and updated the footer; resending the same turn returned the same event (`replayed`); a
  bad exit was rejected with 422. Prose was the fallback narrator (no Vertex credentials).
  In a real RisuAI client (PocketRisu 1.14.0 on 6131, plugin 0.7.0, new chat with no SAGA
  module): `move north` from a room without that exit was rejected with the room's exit keys;
  `move south` moved the player and the footer showed
  `player 20/20 · opponent 30/30 · ⏱ 0s · 📍 지하 호수 · 출구 north, south`. Rollback material: image tag
  `saga-runtime:risu-6131-pre-maps` and a SQLite backup taken before the rebuild.
- 6011 playtest backend (`saga-runtime`, rebuilt with `--no-deps`, PocketRisu untouched): before
  the rebuild, every successful turn in the host's request log (32 turns, all on worlds pinned to
  SRD 0.1.0) re-derived the same event identity with the new code and found its committed
  prefix in a copy of the database; after the rebuild all 5 worlds (17 branches) replay
  unchanged. Rollback material: image tag `saga-runtime:risu-6011-pre-maps` and a SQLite backup.
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

- No rollback after deploy: older code cannot verify worlds created with a map.
- A v3 message starting with "move" is now a command (rejected with help) rather than natural
  language, like `give`/`take`/`heal` already are.
- Natural-language movement ("북쪽으로 간다") needs an interpreter contract change with a tape
  identity design; until then players type `move <exit>` from the footer.
- Benchmark scenarios that spell out a map in `initial_world` are not validated by
  `map_problems`; a dangling exit there would surface as an error at move time.
- Vertex narration with the map block was not exercised live (no credentials on the test
  chat).
- Host pitfalls seen on 6131: a chat with an older SAGA module enabled pins that rulebook, so it
  has no map and `move` is treated as natural language ("Vertex required" error, which is
  misleading there); a rejected message left in the chat makes the next turn 409 until it is
  removed or edited.
- Discovery by checks, locked doors, loot, NPC movement, play-time generation and a visual map
  are later slices (ADR 0009, Not in this slice).
