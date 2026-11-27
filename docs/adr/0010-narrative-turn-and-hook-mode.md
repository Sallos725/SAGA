# ADR 0010: State-Neutral `narrative_turn` Events and Hook-Mode Turn Adjudication

Date: 2026-11-27
Status: **Accepted (M1 Slice 1)**

## Context

Under bridge protocol v1–v3, every non-command user message either requires Vertex intent
interpretation into a narrow SRD roll action (`attack`, `ability_check`, `saving_throw`) or
is rejected with HTTP 422. In real TRPG play inside RisuAI / PocketRisu, most turns are
ordinary roleplay or dialogue that do not require dice rolls or state mutations.

In addition, SAGA's play-first direction ([DIRECTION.md](../DIRECTION.md)) introduces a
RisuAI **Hook mode** (`beforeRequest` / `output`), where RisuAI's own configured main model
(and auxiliary model where available) generates prose with the user's presets and streaming,
while SAGA commits authoritative state/rolls before generation and injects a read-only
context block.

## Decisions

### 1. Reserved runtime event: `narrative_turn`

- `NARRATIVE_TURN_KIND = "narrative_turn"` is handled directly by `RuntimeEngine.dispatch`
  with a closed empty payload (`NarrativeTurnPayload`, `extra="forbid"`).
- `RuntimeEngine` validates that `actor_id` exists in `state.actors` and commits a
  state-neutral `RuleResult`:
  - `state` is unchanged except `seq = next_seq`,
  - `rolls = []`, `patches = []`, `resolution = None`,
  - deterministic `facts` and non-empty `trace`.
- `rules.apply` and `dice` are never called for `narrative_turn`.
- Because `narrative_turn` is handled at the runtime level rather than duplicated in each
  rulebook, every bundled and imported rulebook supports pure RP turns without changing
  rulebook schemas, command grammars, or legacy SRD action identities.

### 2. Bridge protocol v4 and pure RP pass-through

- `HostTurn.schema_version` extends to `Literal[1, 2, 3, 4]`.
- Protocol v1, v2, and v3 retain their exact previous behavior and error contracts.
- Protocol v4 preserves the v2 world namespace, v2 canonical command identities, and v3
  natural-language identities, so existing v2/v3 chats upgrade without migration.
- Under protocol v4:
  - Messages starting with a rulebook command verb still use strict command parsing
    (malformed commands fail with 422 and never silently become `narrative_turn`).
  - Ordinary natural-language messages commit cleanly as `narrative_turn` when no intent
    interpreter is configured, or when an interpreter declines to propose a mechanical
    rule action.
  - Regenerating a committed `narrative_turn` hits `_read_prefix` first and returns
    `replayed: true` without re-invoking intent or mutating state.

### 3. Hook-mode adjudication endpoint (`POST /v1/saga/turn`)

- `RisuBridge.commit_turn` encapsulates world resolution, identity verification, branching,
  validation, journal commit, and optional fail-soft memory hooks.
- `POST /v1/saga/turn` accepts `TurnRequest` (`saga: HostTurn`, optional untrusted
  `client_proposal`) and returns `{"saga": ...}` without invoking a SAGA narrator LLM.
- `client_proposal` (for example from a RisuAI auxiliary model) is strictly validated
  against the world's closed rulebook payload models plus `NarrativeTurnPayload` before any
  world creation or dispatch; forged or unsupported proposals fail with 422. On replay,
  the committed journal entry is returned and any new `client_proposal` is ignored.
- The response includes a read-only `prompt_block` for `beforeRequest` injection containing
  committed facts, rolls, resolution, rendered `display`, the player's known map (`scene`),
  and non-authoritative recalled memories. Per [ADR 0009](0009-locations-and-map-movement.md),
  full `map` data and raw actor `location`/`visited_rooms` are never included.

## Consequences and invariants

- **Rerender immutability**: Regenerating any v4 turn (rule action or `narrative_turn`)
  never runs dice, tools, or intent interpretation a second time.
- **No LLM direct mutation**: Neither RisuAI's main model nor `client_proposal` can mutate
  `WorldState` directly; every commit enters through validated `GameEvent` dispatch.
- **Backward compatibility**: Storage v2 schema, `WorldState` hashes, existing bundled
  rulebooks, and 6011/6131 worlds remain unchanged and replayable.
- **RuleCheeseBench comparability**: `narrative_turn` is not part of any rulebook's command
  grammar (`command_verbs`), so benchmark command parsing and scoring are unaffected.
