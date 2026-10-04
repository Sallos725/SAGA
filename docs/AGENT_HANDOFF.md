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
The no-memory PocketRisu command slice is now implemented. Read
[the integration contract](RISUAI_INTEGRATION.md), [starter guide](../packs/risu-starter/README.md)
and [verification report](progress/risu-command-demo.md). Stable host/chat/message identities
derive chained event ids, and regeneration reads committed results before intent or dice.
A chat appends to one branch and forks only on a divergent edit; the earlier per-turn copied
branches remain readable and continue in place. Storage v2 is unchanged.
The demo supports SRD attack/check/save commands with fallback narration; it is not an LLM
interpreter, full ruleset, or general card/lore adapter. Storage v2 and runtime semantics are
unchanged. The starter can proceed independently while NMOS remains unavailable.

Bridge identity v2 subsequently fixes cosmetic command rerolls and context-hiding resets;
read [the identity fix report](progress/risu-identity-context-fix.md) before extending it.
Plugin 0.2.0 introduced hidden non-comment user turns for committed-prefix validation. Existing
v1 worlds are exact-history read-only, with new v2 play requiring a new host chat. No database
or runtime schema migration was added. This trusted demo is not adversarial benchmark isolation.

Plugin 0.3.0 adds optional Vertex narration after commit. The user pastes service-account
JSON into one plugin field; blank input keeps fallback. SAGA derives the project/global endpoint
and uses `gemini-3.8-flash`. Credentials travel in a host-redacted secret header and remain
request-scoped in SAGA, outside game events/state/journals. The optional typed narration body
contains only bounded context. Invalid JSON/key fails before dispatch; OAuth/model failures
return committed fallback with a generic notice. Standard `google-auth` is an API-extra dependency.
Read the [narration report](progress/risu-vertex-narration.md) and starter guide for timing,
credential handling and evidence. No PocketRisu modification or auxiliary-model configuration
is required. Installation, JSON input rejection, fallback regeneration and two approved live
Gemini calls are verified on 6011; authoritative results stayed unchanged. Exact billed cost
was not observed. Natural-language intent and NMOS remain deferred.

Owner rule for billable provider tests: before every future test, disclose its scope, maximum
call count, estimated cost and assumptions/uncertainty, then obtain explicit approval. Stop
at the approved limit or the first failure; do not make unapproved retries. The recorded
two-call allowance has been consumed. This is an agent workflow rule, not an application UI step.

The owner's oneuldo memory-pilot branch provides a concrete PocketRisu plugin route to
chat/message ids. Read [the integration review](RISUAI_INTEGRATION.md#host-identity-reference-oneuldo-memory-pilot)
before treating turn identity as unavailable: its five focused tests were reproduced.
The SAGA binding uses the current host's `getCharacter()` snapshot instead of the pilot's
numeric `getChatFromIndex()` path; see the integration contract for that compatibility detail.
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

## Third prompt: extend the RisuAI command slice

> The command/provider slice is implemented. First read its integration contract and current-host evidence. Preserve one stable runtime event per unchanged user action and immutable edit branches. A regenerated assistant reply must reuse the committed result and must not reinterpret intent or call dice/tools again.
>
> Opt-in Vertex narration is implemented; check its current host/provider verification in the narration report. An LLM intent binding remains separate work and must produce a validated closed GameEvent. Keep deterministic commands and fallback narration usable without credentials or NMOS. Preserve pre-narration commit and rerender tests. Journal storage is now linear per chat; address full-history requests and per-request prefix verification before claiming long-horizon host operation. General card/lore compatibility and continuation remain separate work.

## Fourth prompt: RuleCheeseBench

> Implement the smallest RuleCheeseBench runner that can execute a scenario through the production runtime, capture all event/tool traces, and evaluate hidden deterministic checks. Add fixed-seed or pre-recorded-roll mode so model comparisons receive equivalent stochastic conditions. Keep judge-only information out of model context. Emit a machine-readable run artifact plus a human-readable report.

## Stop conditions for agents

Ask for human input only if one of these is truly blocking:

- current NMOS integration surface is ambiguous in a way that changes data ownership;
- RisuAI exposes no stable way to distinguish regeneration from a new action;
- a ruleset requires a licensing decision;
- a proposed dependency creates a major operational commitment.

Otherwise make the smallest reversible choice, document it, and continue.
