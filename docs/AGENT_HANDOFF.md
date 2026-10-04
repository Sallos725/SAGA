# Agent Handoff

Copy the prompt below into Codex or Claude Code from the repository root.

Current checkpoint (2026-10-04): SAGA foundation hardening is implemented on
`codex/saga-foundation-hardening`. Read [the hardening report](progress/saga-foundation-hardening.md),
[the persistence contract](PERSISTENCE.md), and [ADR 0005](adr/0005-durable-tool-results.md)
before continuing. Package/import names are `saga-trpg` / `saga_trpg`; storage v1 requires an
explicit backed-up upgrade to v2. Game schema v1 and runtime semantics `0.2.0` are unchanged.
The tape has durable claim/result storage and a tested external-dice repository path.
Actual remote tools and reconciliation of ambiguous executions remain later work.
The [phase 1 report](progress/phase-1.md) is historical evidence, not the current task list.
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
The owner's local `~/saga-ref/dice/` module and plugin were also inspected as source.
Read [the dice reference review](LOCAL_DICE_REFERENCE.md) for file hashes, tool/display
contracts, compatibility details, and the required durable-tape integration boundary.
The plugin was not run; backend transport and live host compatibility remain unverified.

## Recommended next task: NMOS boundary audit

> Read `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md`, and `docs/IMPLEMENTATION_PLAN.md` before coding. Treat the architectural invariants as requirements, not suggestions.
>
> Inspect the current NMOS public/local API and document a minimal `MemoryPort` binding plan.
> Work on a feature branch. Keep NMOS optional; no-memory and fake-memory operation must remain
> possible. Confirm world/actor visibility, provenance, duplicate writes, and fail-soft behavior
> before implementing a real transport. Do not expand RisuAI, rulesets, or the benchmark here.
>
> Run the full CI command set and retain deterministic replay, branch isolation, durable tool
> identity, crash recovery, and rerender immutability. Never inject a non-pure remote tool as
> bare `DicePort`; use `ToolBinding` and the tape. Do not automatically reset a pending claim.

## Subsequent prompt: NMOS integration

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
