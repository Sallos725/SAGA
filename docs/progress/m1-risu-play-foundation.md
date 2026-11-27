# M1: RisuAI Play Foundation

Date: 2026-11-27 (Asia/Seoul).
Status: implemented and verified offline on branch `Sallos725/m1-narrative-turn-and-hook-api`.
Direction: [DIRECTION.md](../DIRECTION.md). Contract: [ADR 0010](../adr/0010-narrative-turn-and-hook-mode.md).

## Completed

### Slice 1 — `narrative_turn`, protocol v4, and Hook-mode adjudication endpoint
- Added reserved state-neutral event `narrative_turn` (`domain/events.py`, `runtime/engine.py`)
  with a closed empty payload (`NarrativeTurnPayload`). It advances `seq` with zero rolls,
  zero patches, deterministic facts/trace, and never calls `rules.apply` or `dice`.
- Extended `HostTurn.schema_version` to `Literal[1, 2, 3, 4]` while preserving v1 read-only
  behavior, v2 command identities, and v3 natural-language identities.
- Extracted `RisuBridge.commit_turn` and added `POST /v1/saga/turn` for Hook mode:
  - Commits commands, validated `client_proposal` (e.g. from RisuAI's auxiliary model),
    configured intent, or `narrative_turn` without running SAGA's narrator LLM.
  - Returns a read-only `prompt_block` containing committed facts, rolls, resolution,
    rendered `display`, the player's known map (`scene`), and non-authoritative memories;
    full `map` and raw actor `location`/`visited_rooms` remain hidden (ADR 0009).
- Exported `schemas/risu-turn.schema.json` and updated `schemas/risu-completion.schema.json`.

### Slice 2 — OpenAI-compatible narrator/intent, `risu_prompt`, and v4 SSE streaming
- Added `OpenAICompatConfig` and `OpenAICompatNarrator` (`adapters/narrator/openai_compat.py`)
  configured via server-side environment variables (`SAGA_LLM_BASE_URL`, `SAGA_LLM_API_KEY`,
  `SAGA_NARRATOR_MODEL`, `SAGA_INTENT_MODEL`, `SAGA_LLM_TIMEOUT_SECONDS`).
- Preserves host-assembled `risu_prompt` messages at the front of narrator requests and
  appends read-only authoritative SAGA grounding after them.
- Added `OpenAICompatIntentTool` (`adapters/intent/openai_compat.py`) over the existing
  durable tool tape (`risu-openai-intent-v1`), classifying natural text into any validated
  action of the world's rulebook or `narrative_turn`, never retrying pending transport errors.
- Added protocol v4 SSE streaming on `POST /v1/chat/completions` (`stream: true`): state
  commits strictly before SSE begins, streams OpenAI delta chunks, falls back cleanly on
  narrator failure, and emits the authoritative `saga` payload before `[DONE]`.

### Slice 3 — RisuAI plugin v0.8.0 (Hook mode + v4 Provider streaming)
- Updated `packs/risu-starter/saga_provider.js` to v0.8.0 with dual operating modes:
  - **Hook mode (`integration_mode=hook`)**: uses RisuAI's own main model (presets &
    streaming) and optional auxiliary model (`use_risu_aux_intent=true` via `api.request`
    with `mode: "submodel"`, `allowPlugins: false`). Auto-activates when a SAGA rulebook
    module is enabled or when toggled ON per chat (`saga:hook-chats:v1`, default OFF).
    Fails closed on SAGA backend errors; injects `prompt_block` in `beforeRequest` and
    appends the escaped authoritative footer in `afterRequest`.
  - **Provider mode (`integration_mode=provider`, default)**: preserves v3 behavior and adds
    opt-in `provider_stream=true` (protocol v4, `risu_prompt`, `ReadableStream` SSE with
    authoritative footer).

## Verification & Self-Review

- Full offline verification: `pytest` (629 passed), `node --test tests/risu_provider.test.cjs`
  (33 passed), `ruff check`, `ruff format --check`, `compileall`, and
  `scripts/export_rulesets.py --check`.
- `CLAUDE.md` 5-point invariant check:
  1. Dice/tools twice on rerender: **No** — `_read_prefix` returns committed entries before
     `client_proposal`, intent, or dice execute; verified in Python and Node tests.
  2. Narration mutating state: **No** — both Hook mode (`prompt_block`) and Provider mode
     (including SSE streaming) commit strictly before prose generation.
  3. Wall-clock in replay: **No** — `narrative_turn` is pure and replays identically across
     SQLite restarts.
  4. NMOS duplication: **No** — NMOS remains optional and deferred per `DIRECTION.md`.
  5. Benchmark comparability: **No** — `narrative_turn` is not in rulebook `command_verbs`
     and benchmark scenarios/drivers are unchanged.

## Unresolved / Next

- Live browser/host verification of plugin v0.8.0 Hook mode (`beforeRequest` + `afterRequest`
  + `registerButton` + `api.request` submodel availability on PocketRisu 1.14.0 / RisuAI)
  and live OpenAI-compatible streaming test on 6131.
- M2: separate scenario card initial-world schema from rulebooks, expand natural-language
  action grounding (`move` + rulebook actions), and design A1 GM free-action adjudication.
