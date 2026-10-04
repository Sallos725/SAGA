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
- [x] Add closed-schema deterministic command interpretation (LLM interpretation deferred).
- [ ] Stream narration if practical, after the state commit.
- [ ] Preserve normal card prompt/lore behavior where compatible.
- [x] Render authoritative HP, rolls and event id from the committed response.

Exit criterion: regenerate the same assistant turn 10 times; journal length, HP, and dice remain unchanged.

The no-memory command slice is independent of Phase 2. See
[the integration contract](RISUAI_INTEGRATION.md) and
[verification report](progress/risu-command-demo.md). Streaming, natural-language/card/lore
integration and efficient long-history branch storage remain open; the demo caps active
history at 256 user actions.

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
