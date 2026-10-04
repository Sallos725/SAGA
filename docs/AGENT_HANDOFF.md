# Agent Handoff

Copy the prompt below into Codex or Claude Code from the repository root.

Current checkpoint (2026-10-04): the first implementation prompt's persistent runtime slice
is implemented. Read [the phase 1 report](progress/phase-1.md) and
[the persistence contract](PERSISTENCE.md) before continuing. The broader roadmap still has
work beyond the implemented examples; NMOS and RisuAI bindings remain later phases.
System-sized, openly licensed ruleset examples were subsequently added. Read
[the ruleset example report](progress/ruleset-examples.md) and
[the source-pack guide](../packs/rulesets/README.md) for supported mechanics and remaining limits.
The owner confirmed Aethrion as an original inspiration. Its source was reviewed at a
pinned revision; see [REFERENCES.md](REFERENCES.md) for the MIT license, RisuAI bridge
and narration references, and differences from SAGA's journal/tool guarantees.
The owner's oneuldo memory-pilot branch provides a concrete PocketRisu plugin route to
chat/message ids. Read [the integration review](RISUAI_INTEGRATION.md#host-identity-reference-oneuldo-memory-pilot)
before treating turn identity as unavailable: its five focused tests were reproduced,
but the plugin-to-SAGA mapping and current-host end-to-end test remain to be implemented.

## Recommended first implementation prompt

> Read `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md`, and `docs/IMPLEMENTATION_PLAN.md` before coding. Treat the architectural invariants as requirements, not suggestions.
>
> Your first goal is to turn this scaffold into a production-shaped vertical slice without overbuilding it. Work on a feature branch.
>
> 1. Audit the scaffold for correctness and fix any obvious defects first.
> 2. Implement a persistent world repository using append-only JSONL + periodic snapshots (SQLite is acceptable instead if you can justify it, but do not add infrastructure without need).
> 3. Guarantee idempotent dispatch by stable event id/idempotency key.
> 4. Add branch/replay support sufficient to rewind to an earlier committed event and fork a new branch without mutating the original history.
> 5. Add tests proving: restart recovery, replay equality, no duplicate dice/state mutation on repeated event id, and branch isolation.
> 6. Do not implement NMOS or RisuAI deeply yet; keep their ports stable and note any contract changes needed.
> 7. Run tests/lint, self-review against `AGENTS.md`, and write `docs/progress/phase-1.md` describing changes, trade-offs, and remaining risks.
>
> Do not silently weaken determinism for convenience. Do not let LLM output become authoritative state.

## Second prompt: NMOS integration

> Inspect the CURRENT `Sallos725/NMOS` repository and its supported local/public integration surface before writing code. Do not rely on stale assumptions or copy NMOS internals.
>
> Implement `MemoryPort` through `NMOSAdapter` with world scoping, actor/epistemic scoping where the real NMOS API supports it, event-id provenance, timeout handling, duplicate-write behavior, and fail-soft retrieval. If NMOS lacks a required primitive, add the smallest adapter-side mapping and document the mismatch instead of forking NMOS functionality into this repo.
>
> Add integration tests using a fake transport and, if practical, an opt-in live NMOS test. Exact HP/inventory/quest values must remain authoritative runtime state. Memory failure must not corrupt a valid committed game event.

## Third prompt: RisuAI vertical slice

> Implement the RisuAI/OpenAI-compatible outer adapter. First verify the CURRENT request/regeneration behavior of RisuAI's Custom API. Map one user action to one stable runtime event. A regenerated assistant reply must reuse the existing committed event/result and must not call dice/tools again.
>
> Add a closed-schema intent interpreter boundary; it may initially be a deterministic fake for tests. Commit state before narration. Support a fallback narrator. Add an end-to-end test where the same RisuAI-style turn is regenerated repeatedly and journal length, HP, and roll records remain unchanged.

## Fourth prompt: RuleCheeseBench

> Implement the smallest RuleCheeseBench runner that can execute a scenario through the production runtime, capture all event/tool traces, and evaluate hidden deterministic checks. Add fixed-seed or pre-recorded-roll mode so model comparisons receive equivalent stochastic conditions. Keep judge-only information out of model context. Emit a machine-readable run artifact plus a human-readable report.

## Stop conditions for agents

Ask for human input only if one of these is truly blocking:

- current NMOS integration surface is ambiguous in a way that changes data ownership;
- RisuAI exposes no stable way to distinguish regeneration from a new action;
- a ruleset requires a licensing decision;
- a proposed dependency creates a major operational commitment.

Otherwise make the smallest reversible choice, document it, and continue.
