# Agent Handoff

Copy the prompt below into Codex or Claude Code from the repository root.

Current direction checkpoint (2026-11-27): read [DIRECTION.md](DIRECTION.md) first before
picking new work. Active priority is **M1 — RisuAI play foundation** (play-first TRPG
runtime using SAGA scenario cards + rulebook modules, `narrative_turn` no-op events for
pure RP turns, RisuAI Hook mode using RisuAI main/auxiliary models alongside extended
OpenAI-compatible/streaming Provider mode). NMOS live/visibility/ablation, MCP external
dice, live RuleCheeseBench runs, and Presentation/TTS are explicitly deferred until the
core play loop is solid. Keep existing 6011/6131 worlds and offline tests green.

Earlier checkpoint (2026-10-12): locations and map movement v1
([ADR 0009](adr/0009-locations-and-map-movement.md), [report](progress/locations-map-v1.md))
is merged and deployed on 6131 and 6011 (`srd-5.2.1@0.6.0`, `fate-condensed@0.5.0`).

Earlier checkpoint (2026-10-06, later): rulebooks are data. Read
[ADR 0007](adr/0007-rulebooks-as-data.md), [the creator guide](RULEBOOKS.md) and
[the rulebooks report](progress/rulebooks-v1.md) before touching rules: new system
behaviour goes into a rulebook JSON interpreted by `saga_trpg.rulebooks.engine`, not into
Python under `modules/`. `srd-5.2.1@0.6.0` is the latest bundled rulebook (earlier data versions stay bundled byte for byte); `0.1.0`–`0.3.0` stay
registered as Python oracles for pinned worlds and the equivalence test. Scripts in
rulebooks remain closed pending a further ADR. Rulebooks ship as RisuAI modules
([RISUAI_MODULES.md](RISUAI_MODULES.md), [ADR 0008](adr/0008-rulebooks-in-risuai-modules.md));
the backend imports them at `POST /rulebooks`, plugin 0.6.0 forwards an enabled module's
rulebook with each turn, and commands are derived from the world's rulebook
(`saga_trpg.rulebooks.commands`). The NMOS sidecar 0.3.0 is bound behind `MemoryPort`
(`docs/NMOS_SIDECAR.md`): the bridge writes committed facts after each commit and recalls
before narration, fail-soft, off unless `SAGA_NMOS_URL` is set; a live round trip is an
operator decision. RuleCheeseBench runs on the production runtime with scripted and Vertex
drivers and a comparability-checked report ([progress](progress/rulecheesebench-v2.md)); no
live model run yet. To write
or extend a rulebook with a creator, use the `rulebook-author` skill in `.claude/skills/`.

Earlier checkpoint (2026-10-06): SRD `0.2.0` state actions (damage, heal, conditions, time)
and their PocketRisu commands are merged. Read [ADR 0006](adr/0006-ruleset-versions-and-state-actions.md)
and [the state-actions report](progress/srd-state-actions.md) first: ruleset versions are
pinned per world and registered side by side, new worlds take the latest, nothing happens
automatically at 0 HP, and the 6011 runtime still runs the older image. `0.3.0` adds
inventory counts. Open design question before adding more SRD logic in Python: the owner
wants rulebooks delivered as RisuAI modules over a generic core, so condition effects and
similar rules should become validated ruleset data that the engine interprets; whether
modules may also carry sandboxed scripts is undecided and needs an ADR.

Earlier checkpoint (2026-10-04): SAGA foundation hardening is implemented on
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
The initial demo supported SRD attack/check/save commands with fallback narration; the
limited natural-language extension is described below. It is not a full ruleset or general
card/lore adapter. Storage v2 and runtime semantics are
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
was not observed. Plugin/card 0.4.0 now adds natural-language attack/check/save through the same Vertex JSON
field. Read [the natural-intent report](progress/risu-natural-intent.md) for the closed proposal,
explicit target/DC requirements, protocol 3, preserved v2 world/command identity and v1
read-only compatibility. Existing committed-event replay is unchanged; new precommit
interpretation reuses the existing durable tool tape on main with event id and parent-state
hash. No new cache/schema or PocketRisu changes were made. Pending interpretation never
reissues automatically. 255 Python and 22 Node tests and an independent shipped-plugin →
actual API/SQLite/SDK harness passed with simulated Google boundaries. No new live calls or
6011 upgrade occurred; actual semantic quality and host UI remain unverified. NMOS remains deferred.

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
> Opt-in Vertex narration is implemented; check its current host/provider verification in the narration report. The bounded Vertex intent binding now produces validated closed attack/check/save proposals
> through the existing durable tape; preserve its precommit claim/result and pending no-retry contract. Keep deterministic commands and fallback narration usable without credentials or NMOS. Preserve pre-narration commit and rerender tests. Journal storage is now linear per chat; address full-history requests and per-request prefix verification before claiming long-horizon host operation. General card/lore compatibility and continuation remain separate work.

## Fourth prompt: RuleCheeseBench

> Implement the smallest RuleCheeseBench runner that can execute a scenario through the production runtime, capture all event/tool traces, and evaluate hidden deterministic checks. Add fixed-seed or pre-recorded-roll mode so model comparisons receive equivalent stochastic conditions. Keep judge-only information out of model context. Emit a machine-readable run artifact plus a human-readable report.

## Stop conditions for agents

Ask for human input only if one of these is truly blocking:

- current NMOS integration surface is ambiguous in a way that changes data ownership;
- RisuAI exposes no stable way to distinguish regeneration from a new action;
- a ruleset requires a licensing decision;
- a proposed dependency creates a major operational commitment.

Otherwise make the smallest reversible choice, document it, and continue.
