# ADR 0009: Locations as a room graph in authoritative state

Status: proposed (2026-10-12). Tracks [SAGA-44](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/cc780fc2-1b0b-419d-90e1-c2c69416c523/)
(A5 in [the TRPG feature review](../TRPG_FEATURE_REVIEW.md)); this ADR covers its first slice only.

## Problem

A TRPG needs a place to stand. `WorldState` has actors, flags and quest state but no notion
of where anyone is, and no rulebook has a movement rule. Without that, the narrator invents
the layout each turn and contradicts itself on revisits.

Handing the model a map tool (an MCP server it calls on its own) does not fit this runtime:
a reroll can call the tool again and produce a different map, a `save_map` call lets the model
write state, and replay depends on what the tool returned. A map is a pure function of a seed
and belongs in the deterministic core, not behind a tool port or in NMOS
([ADR 0001](0001-state-vs-memory.md), [ADR 0005](0005-durable-tool-results.md)).

## Decision

**The map is a room graph in authoritative state.** `WorldState.map` (optional) holds:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `start_room` | Room id where actors without a placement begin |
| `rooms` | `id → {name, description, exits}` |
| `rooms.<id>.exits` | `key → {to_room, label, kind}`. `key` is a lower-case identifier unique within the room (`north`, `up`, `cellar_stairs`); `label` is display text; `kind` is `open` (archway, corridor) or `door`. Exits are directed; a two-way passage is one exit in each room. |
| `generator` | `{name, version, params}` when generated, absent when hand-authored |

Each actor gains `location` (room id) and `visited_rooms` (set of room ids). In a world with a
map, an actor without an explicit placement starts in `start_room`, and every actor's starting
room counts as visited. There are no
coordinates, distances or line-of-sight rules; tactical grids are out of scope.

**State additions are hash-neutral when empty.** `stable_model_hash` covers the whole dump, so
a plain new field with a default would change the hash of every stored state and fail
snapshot verification for existing worlds. `map`, `location` and `visited_rooms` are omitted
from serialization while unset or empty. The omission is value-based (a wrap serializer drops
`None` and empty values), not `exclude_unset`, because rules build states with
`model_copy(update=...)`; it applies to both `model_dump()` (hashing) and `model_dump_json()`
(storage). `visited_rooms` is serialized sorted, like `conditions`. A world without a map dumps, stores and hashes
byte-for-byte as before; `RUNTIME_VERSION` and the storage version do not change. This is the
rule for any future optional state field.

**Maps exist from world creation.** A rulebook's `example_world.map` is either a hand-authored
`{start_room, rooms, placements}` or `{generate: {seed, rooms: [min, max]}}`.
`placements` maps actor ids to room ids ("the wolf is in the guard room"); it is optional, only
places actors and never moves them later. Generated room ids are not known in advance, so a
generated map has no placements and every actor starts in `start_room`. `example_world` builds
the map when the world is created and the result is stored in the initial state. Replay starts
from that stored state and never reruns the generator, so later generator changes cannot
alter an existing world. Generating new areas during play is not part of this slice.

**The generator is a pure function in `domain/`.** Seed material is
`"<declared seed>|<world_id>"`, hashed with SHA-256 as the reference dice do; each RisuAI chat
(a world) therefore gets its own dungeon, and the same chat always gets the same one. The
room count is drawn from `rooms: [min, max]` within 2..30. The generator builds a random
spanning tree over the rooms so every room is reachable from `start_room`, then makes
`room_count // 4` attempts at an extra connection for loops (an attempt is skipped when the
two rooms are already linked or share no free direction). Each connection becomes two exits along one
direction pair (north/south, east/west, up/down) that is still free in both rooms; the
direction is the exit key, so a room has at most six exits and labels read consistently both
ways ("north door" / "south door").
An exit pair is a `door` with probability 1/3, otherwise `open`. Room names and exit labels
come from a built-in table (Korean in this slice, matching the RisuAI host); the start room is
always the table's entrance hall and names do not repeat within a map. `generator.version` names the algorithm and table revision.

**Movement is the core builtin `map_move`.** A rulebook opts in by declaring an action with
`builtin: map_move` (input: `exit`, an exit key). Command parsing stays stateless: the event
payload carries the key as given, and `map_move` resolves it against the actor's current room
in `state` when the event is applied, so parsing and replay never depend on where the actor
stands. The rule accepts the move only if the world has a map, the actor has a location and
the current room has an exit with that key. It sets `location` to the
exit's `to_room`, adds the room to `visited_rooms`, and records patches, a fact and a trace
like any builtin. An invalid move raises the same validation error as other builtins and is
rejected without a journal entry. Moving does not advance simulated time. The closed command
is `move <exit key>` (`move north`); labels are not matched because they contain spaces and
the command grammar is positional. Natural-language movement ("북쪽으로 간다") is not
part of this slice: the interpreter is bound to SRD roll actions and to the durable tape's
tool identity, and changing its contract needs its own design so that rerolling an old turn
can never call the model again. Until then the footer lists the exit keys a player types.
New bundled versions `srd-5.2.1@0.6.0` (with a generated example map) and
`fate-condensed@0.5.0` (with a small hand-authored map) declare the action; earlier versions
are unchanged.

**What the player and the narrator see is a projection.** `known_map(state, actor_id)` is pure:

- the current room shows its name, description, the other actors in it and its exits;
- an exit's destination shows its name if the destination was visited or the exit is `open`,
  otherwise `???`;
- visited rooms are listed by name;
- nothing seen-but-unvisited is stored; the projection is recomputed from state each time.

The rendered block is a plain list (current room, occupants, exits, visited rooms), which models
read more reliably than ASCII art of a graph. It goes into the narrator prompt as its own part,
and the committed state the narrator receives has `map` removed. A rulebook can add `location`
to `display.world` so the RisuAI footer shows the player's room and exit keys on one line
(`📍 입구 홀 · 출구 east, north`). The bridge response's machine-readable `state` still carries
the full map for the host plugin, which prints only prose and the footer.

The narrator receives the projection, not the full map. In table play the GM knows the whole
dungeon, but here judging is the runtime's job and narration is the model's, and a model given
"treasure in the storeroom (undiscovered)" tends to leak it into prose. A deliberate GM-only
hint field can be added later if a design needs one.

**Validation happens when a rulebook is checked.** Room ids and exit keys that are not
lower-case identifiers, exits to unknown rooms, a missing start room, rooms unreachable from the start, actors placed in unknown rooms
and out-of-range generator parameters are reported by entry path in plain language, and an
invalid map stops world creation.

## Consequences

- Revisiting a room returns the same name, description and exits; the narrator cannot move
  walls between turns.
- Rerendering reads committed state only. The map is never generated, and no move is applied,
  during regeneration.
- Existing worlds and their pinned rulebook versions replay unchanged; a golden-hash test
  pins that.
- NMOS is unaffected. Moves reach memory through the existing semantic projection (action kind
  and text inputs), and the map itself is never stored there.
- Benchmark runs stay comparable. The runner builds worlds from the scenario's
  `initial_world` (world id `bench-<scenario>`), not from `example_world`, so the generator
  never runs there; a scenario that needs a map spells it out, and it is part of the recorded
  initial world.
- `srd-5.2.1@0.6.0` becomes the latest bundled SRD, which is what new RisuAI chats pin by
  default. New chats therefore start in a generated dungeon; existing chats keep their pinned
  version and have no map. This is a product change, not only an added feature.
- Room names come from one built-in table in this slice. Themed tables are a later parameter.

## Not in this slice

Natural-language movement, discovering hidden exits by a check, locked doors, one-time loot, generating areas during
play, NPC movement, time cost of travel, and a visual map (SVG, Mermaid or an HTML view
rendered from the same projection, shown when a player asks to see the map). Each needs its own
decision on how check outcomes, records and the projection interact.

## Evidence (to be added with the implementation)

Planned tests, starting with the hash-neutrality golden test (the hash and stored JSON of a
map-less world are unchanged, and history verification still passes) before any other code:
same seed and world give the same map and different worlds differ; reachability
holds across several hundred seeds; moves are accepted and rejected as specified; journal,
replay and rerender leave state unchanged; the projection hides unvisited rooms; the hash of
a map-less world is unchanged (first test above).
