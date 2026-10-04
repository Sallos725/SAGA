# Implementation Plan

This is the recommended order for Codex/Claude Code. Do not implement everything in parallel before the contracts stabilize.

## Phase 0 — lock invariants

- [x] Define `GameEvent`, `WorldState`, `RuleResult`, `RollRecord`.
- [x] Define deterministic reference dice.
- [x] Define append-only journal interface.
- [x] Add idempotent event replay test.
- [x] Add state schema migration/version policy (reject unknown versions; explicit migration only).
- [x] Add crash-consistency test for persistent journal.

Exit criterion: state can be reconstructed from committed records without rerunning tools.

## Phase 1 — persistent runtime

- [x] Replace demo in-memory registry with a world repository.
- [x] Add snapshot + journal compaction policy (periodic snapshots, retain all history).
- [x] Add branch metadata and rewind/branch API.
- [x] Add event validation registry for new example rulesets (legacy scaffold remains unchanged).
- [x] Add built-in ruleset manifests with compatibility versions (external plugin loading deferred).
- [x] Add explicit storage v1 → v2 migration with backup and replay validation.
- [x] Complete SAGA package identity and CI on Python 3.11–3.13.
- [ ] Add structured trace IDs and correlation IDs.

Exit criterion: server restart preserves exact world and event history.

## Phase 2 — NMOS adapter

First inspect the current NMOS repository/API. Do not guess old endpoints.

Implement:

- [ ] health/capability detection
- [ ] `remember(MemoryWrite)`
- [ ] `retrieve(...)`
- [ ] world scoping
- [ ] actor/epistemic scoping
- [ ] source event id metadata
- [ ] timeout + fail-soft behavior
- [ ] duplicate-write/idempotency behavior
- [ ] tests with a fake NMOS server/adapter

Do not:

- vendor NMOS internals
- create a competing embedding store
- use NMOS as exact game state

Exit criterion: a 100+ turn synthetic campaign can retrieve an early clue without state correctness depending on retrieval.

## Phase 3 — RisuAI adapter

- [x] Document pinned PocketRisu 1.13.0 provider/hook behavior and completion subset.
- [x] Add session/world identity mapping.
- [x] Add stable mapping from user turn to runtime event id.
- [x] Detect regeneration/rerender without redispatch.
- [x] Add closed-schema deterministic command interpretation.
- [x] Add bounded Vertex natural attack/check/save proposals using the existing durable tape.
- [ ] Verify actual natural-language model quality and upgraded 6011 UI with approved calls.
- [x] Add opt-in Vertex narration from one JSON input after commit, with bounded waiting and fallback.
- [ ] Stream narration if practical, after the state commit.
- [ ] Preserve normal card prompt/lore behavior where compatible.
- [x] Render authoritative HP, rolls and event id from the committed response.

Exit criterion: regenerate the same assistant turn 10 times; journal length, HP, and dice remain unchanged.

The no-memory command slice is independent of Phase 2. See
[the integration contract](RISUAI_INTEGRATION.md) and
[verification report](progress/risu-command-demo.md). Streaming and general card/lore
integration remain open; the limited natural-language adapter is offline verified. Consecutive turns append to one branch, so journal storage is linear;
requests still submit and verify the full history, capped at 256 user actions.

Plugin 0.3.0 optionally narrates through Vertex using one user-pasted service-account JSON
input. SAGA derives the project/global endpoint and uses Gemini 3.8 Flash after commit.
Regeneration can change prose while retaining the event, state and rolls. Only bounded textual
host context is copied; this does not complete general card/lore compatibility. See the
[narration report](progress/risu-vertex-narration.md) for the optional request contract,
secret-handling tests and two successful approved live calls. No PocketRisu code/API/image change or
auxiliary-model configuration is needed. Plugin 0.4.0 adds limited natural-language intent before commit with the same JSON input,
protocol 3, unchanged v2 command identities and durable precommit result reuse. See the
[natural-intent report](progress/risu-natural-intent.md). Live language quality and NMOS remain open.

Bridge identity v2 now canonicalizes cosmetic command variants, retains committed context-hidden
turns, and keeps legacy v1 worlds exact-history read-only. See
[the review-fix report](progress/risu-identity-context-fix.md) before extending these contracts.

## Phase 4 — MCP/tool layer

- [ ] Select a concrete MCP dice server.
- [ ] Bind `MCPDiceAdapter`.
- [ ] Define timeout/fallback policy.
- [x] Persist external tool claims/results before committing events; replay recorded results.
- [x] Distinguish pure deterministic dice from taped external tools.
- [ ] Add provider-specific reconciliation for ambiguous pending executions.
- [ ] Add tool capability manifest.

External mutable tools require extra care: replay should normally use recorded results, not query the new present-day value.

## Phase 5 — modular TRPG rules

Start tiny.

- [ ] generic checks
- [ ] attacks/damage/heal
- [ ] inventory
- [ ] conditions
- [ ] clocks/time
- [ ] quest flags
- [ ] visibility/observation
- [x] system-sized example module manifests + campaign selection

Only after those are stable:

- [x] SRD-derived example modules, respecting licensing (SRD 5.2.1 and Fate Condensed subsets)
- [ ] social/relationship module
- [ ] encounter tables
- [ ] card-specific extension hooks

## Phase 6 — RuleCheeseBench

- [ ] scenario schema
- [ ] hidden validators
- [ ] fixed-seed/pre-recorded roll mode
- [ ] action legality validator
- [ ] model driver
- [ ] run artifact format
- [ ] replay viewer/report
- [ ] long-horizon memory ablations

Recommended ablations:

1. model only / short context
2. raw long chat history
3. summary memory
4. NMOS retrieval
5. NMOS retrieval with actor-specific visibility

Exit criterion: two models can run the exact same scenario with comparable state/tool conditions and inspectable traces.
