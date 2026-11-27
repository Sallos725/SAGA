# SAGA Direction and Play-First Roadmap

Date: 2026-11-27 (Asia/Seoul).
Status: **Accepted product direction**.
Scope: Prioritization and delivery plan on top of the existing architecture in
[ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](../AGENTS.md). Individual schema,
event, or plugin contract changes introduced in M1–M3 still require versioned contracts
and ADRs where semantics change.

## Primary objective

SAGA's first priority is to be a **playable, enjoyable deterministic TRPG runtime inside
RisuAI / PocketRisu**.

RuleCheeseBench remains supported on the same deterministic runtime as a secondary
consumer; existing benchmark invariants and comparability rules stay intact, but near-term
engineering prioritizes real RisuAI play quality over live benchmark campaigns.

## Product and architectural decisions

### 1. Content model: SAGA scenario cards + rulebook modules

- The primary content model pairs a **SAGA scenario card** (world setting, initial NPCs,
  map, card lore) with a **rulebook module** (`.risum` / `risu-module.json` defining stats,
  checks, actions, conditions, items, and display).
- RisuAI's native play ergonomics—presets (`risu_prompt`), streaming, lorebooks, regex,
  and visual/status post-processing—must be preserved rather than discarded.
- Arbitrary third-party RisuAI cards are **not** live-parsed into authoritative
  `WorldState` during turns. If support for existing third-party cards is wanted later, it
  should be an offline or assisted conversion tool that emits a SAGA scenario card.

### 2. Relationship with CAST

- SAGA develops as an **independent application and RisuAI integration**, not merged into
  CAST.
- SAGA adopts proven RisuAI integration patterns (preserving `risu_prompt`, OpenAI-compatible
  multi-model endpoints, server-side keys, streaming) while keeping clean HTTP boundaries
  for future interoperability.
- Adding Hook mode (`beforeRequest` / `output`) also leaves room for SAGA to coexist with
  any RisuAI main provider (theoretically including CAST) without forcing a coupled stack.

### 3. Plugin dual mode and LLM connectivity

SAGA supports two complementary RisuAI plugin operating modes:

1. **Hook mode (`beforeRequest` / `output`, new primary play path)**
   - Uses RisuAI's configured **main model** (preserving user presets, provider settings,
     and streaming) and **auxiliary model** where host plugin APIs support it.
   - Before generation, `beforeRequest` sends the turn to SAGA, commits authoritative
     state/rolls, and injects read-only committed facts, state, and known-map context into
     the prompt.
   - Regeneration reuses the committed journal entry without re-rolling dice or
     re-interpreting intent, letting RisuAI's main model regenerate prose only.
   - **Activation**: automatically enabled in chats with an active SAGA module/rulebook,
     plus a per-chat manual ON toggle (default OFF for ordinary chats).
   - **Failure policy**: **fail-closed** in active SAGA chats—warn and abort generation
     rather than silently continuing without authoritative adjudication.

2. **Provider mode (`addProvider`, retained and extended)**
   - Add **OpenAI-compatible endpoints** (OpenRouter, local Ollama, etc.) configured via
     server-side environment variables, with **streaming** narration, `risu_prompt` context,
     and per-role model split (intent/adjudication vs. narration).
   - Keep the existing Vertex service-account path and deterministic no-LLM fallback
     compatible.

### 4. Natural language, pure RP turns, and journal semantics

- Ordinary RP / dialogue turns that require no rule resolution must pass smoothly without
  producing a 422 error.
- Each pure RP turn is committed to the journal as a **state-neutral no-op event** (such
  as `narrative_turn`, with zero state patches and zero rolls). This keeps the per-chat
  turn hash chain unbroken and guarantees that regenerating a pure RP turn reuses the
  committed decision without re-invoking the intent interpreter.
- Rollout order:
  1. Pure RP pass-through (`narrative_turn`) + closed commands.
  2. Natural-language mapping across rulebook actions (including `move`).
  3. GM adjudication stage for creative free actions
     ([TRPG_FEATURE_REVIEW.md](TRPG_FEATURE_REVIEW.md), A1).

### 5. Explicitly deferred (frozen for now)

To keep scope focused on the playable loop, the following tracks are explicitly paused
until the play loop is solid:

- **NMOS**: Keep `MemoryPort` and `NMOSAdapter` intact and opt-in (`SAGA_NMOS_URL` unset).
  Defer live round trip (`SAGA-8`), actor-scoped visibility (`SAGA-7`), long-campaign
  recall (`SAGA-9`), and memory ablations (`SAGA-32`) until real 50–100+ turn campaigns
  need semantic recall.
- **MCP external dice (`SAGA-19`–`22`)**: Local deterministic dice and the durable tape
  remain sufficient for now.
- **RuleCheeseBench live runs and judges (`SAGA-31`, `SAGA-33`)**: Keep the offline suite
  green; defer paid live runs and judge tooling.
- **Presentation / TTS / media (`SAGA-37`, `SAGA-38`)**: Deferred.

## Three-milestone roadmap

### M1 — RisuAI play foundation *(active)*

- Add a state-neutral `narrative_turn` (no-op) event so ordinary RP turns commit cleanly
  and replay/rerender without re-running intent.
- Add OpenAI-compatible LLM support (server `.env` keys, per-role intent vs. narration
  models, `risu_prompt` context, streaming) while keeping Vertex and deterministic
  fallback working.
- Add RisuAI **Hook mode** (`beforeRequest` / `output`, SAGA-module auto-activation +
  per-chat manual ON default OFF, fail-closed) with RisuAI main/auxiliary model support.
- Address host UX rough edges observed during the 6131 deployment check (clearer errors
  when an older rulebook module is attached, smoother recovery after rejected turns).

### M2 — Scenario cards and natural-language play

- Separate **scenario card** initial world data (NPCs, starting map, flags/quests) from
  **rulebook** mechanics so creators can mix scenarios and rulesets cleanly.
- Extend natural-language intent across rulebook actions (including `move`) over the
  durable tool tape.
- Design and ship the first slice of GM free-action adjudication (A1) with an ADR.

### M3 — Exploration depth, UI, and GM controls

- Authoritative roll/result cards and active scene participant board (B1·B2), plus
  in-plugin settings and trace inspection.
- Map v2 (inspection checks, locked exits, loot, NPC movement; follow-up to
  [ADR 0009](adr/0009-locations-and-map-movement.md)).
- Quest tracking and explicit GM correction / scene settlement (A3).

## Non-negotiable invariants

This direction does not relax any hard rule in [AGENTS.md](../AGENTS.md) or
[CLAUDE.md](../CLAUDE.md):

1. `domain/` and deterministic `runtime/` remain free of network I/O, filesystem I/O,
   wall-clock reads, and unseeded randomness.
2. No LLM (RisuAI main/aux, OpenAI-compatible, or Vertex) ever mutates `WorldState`
   directly; every mutation enters through a validated `GameEvent`.
3. Rerender / regeneration never executes dice, tools, or intent interpretation twice.
4. Existing committed worlds (including 6011 and 6131 deployments) continue to replay
   unchanged.
