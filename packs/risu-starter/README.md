# SAGA PocketRisu / RisuAI starter

The SAGA plugin (`saga_provider.js`, **v0.11.2**, Plugin API **3.0**, protocol **v4**) connects
RisuAI / PocketRisu chats to a SAGA runtime for deterministic dice, authoritative state tracking,
replayable branching, and reroll-safe regeneration.

It works out of the box with zero API keys (command + deterministic fallback) and supports two
operating modes:

1. **Hook mode (`integration_mode=hook`, recommended for full RisuAI play)**
   - Keeps your own RisuAI **main model**, presets, lorebooks, and streaming untouched.
   - Auto-activates whenever a SAGA rulebook/scenario module is enabled in a chat, or when manually
     toggled ON per chat from the SAGA button/panel (default OFF for ordinary chats).
   - Before generation (`beforeRequest`), SAGA commits the turn via `POST /v1/saga/turn` and
     injects a read-only `[SAGA Hook Context]` grounding block (facts, rolls, state, known map).
   - Optionally queries RisuAI's auxiliary model (`use_risu_aux_intent=true`) to propose a structured
     action or `narrative_turn` for SAGA to validate before commit.
   - After generation (`afterRequest`), appends a compact, escaped authoritative footer (B1 turn
     card outcome/deltas + display lines). Regeneration reuses the committed journal entry without
     re-rolling dice or re-running intent interpretation.
   - **Fail-closed** in active SAGA chats: if the backend is unreachable or returns an error,
     generation stops with an error instead of silently drifting out of sync.

2. **Provider mode (`integration_mode=provider`, default for quick command demo)**
   - Registers the **SAGA SRD Demo** model (`pluginmodel:::SAGA SRD Demo`) and routes requests to
     `POST /v1/chat/completions`.
   - Supports protocol v4 **SSE streaming** (`provider_stream=true`, forwarding `risu_prompt`),
     server-side **OpenAI-compatible LLMs** (OpenRouter, Ollama, vLLM via `.env`), optional
     **Vertex service-account JSON** narration/intent, or zero-API-key deterministic fallback.

> **Verification status:** Core command/provider flows and Hook-mode natural-language intent
> (`runLLMModel` submodel) + deterministic dice + reroll reuse (`v0.11.2`) have been verified live
> on PocketRisu 1.18.0. The full plugin/runtime suite passes 649 Python + 39 Node tests; broader
> scenario and card-converter live playtesting remains ongoing.

## Install

1. Start SAGA (locally with `uvicorn saga_trpg.api.app:create_app --factory --port 4850` or via
   [deploy/README.md](../../deploy/README.md)).
2. Open RisuAI / PocketRisu → **Settings → Plugins** and import `saga_provider.js`. Allow the
   requested provider/replacer/UI permissions.
3. Configure `server_url` (e.g., `http://127.0.0.1:4850` or `http://saga-runtime:4850` in Docker)
   and set a stable `host_instance_id` (any token without whitespace, e.g. `my-risu-host`).
   - You can also configure these from the fullscreen **SAGA settings & status panel** registered
     on the chat toolbar and plugin settings.
   - Keep `host_instance_id` constant for the same installation; changing it starts a fresh campaign
     namespace.
4. Set `useSayNothing=false` in host settings so an empty Continue action does not inject a
   synthetic `*says nothing*` turn.
5. Choose how you want to play:
   - **Hook mode**: set `integration_mode=hook`, keep your preferred RisuAI main model, enable a
     SAGA rulebook/scenario module (such as `packs/rulesets/srd-5.2.1/risu-module.json` or
     `packs/rulesets/fate-condensed/risu-module.json`) or convert an existing card from the in-panel
     **Card Converter**, and start chatting.
   - **Provider mode**: import `saga-srd-demo.card.json` (or build a `.charx` with
     `python -m saga_trpg.rulebooks charx`), select **SAGA SRD Demo** as your main model, and send
     `attack opponent`.

Disable the plugin when using another main provider in Provider mode if you do not want the global
main-request hook active. In Hook mode, ordinary chats without a SAGA module or manual toggle are
left untouched.

## In-panel UI, Intent LLM, Scene Board & Card Converter (v0.9.0–v0.11.2)

Clicking the SAGA icon in the chat toolbar opens the fullscreen SAGA panel:

- **Settings & Health**: configure `server_url`, `host_instance_id`, `integration_mode`,
  `provider_stream`, per-chat Hook toggle, and check `/health` / `/v1/saga/status`.
- **Natural-language Intent / Dice Classifier (Hook mode)**: pick between **RisuAI submodel**
  (`risuai.runLLMModel`, backed by read-only `POST /v1/saga/intent-context` so rerolls and commands
  make zero auxiliary calls), **Direct OpenAI-compatible LLM** (Base URL, Model ID, API key sent
  only via `x-saga-llm-api-key` header, `reasoning_effort`, custom headers/body), or **Server
  `.env` / Vertex JSON**.
- **B1 Turn Card & B2 Scene Board**: inspect the latest committed turn's outcome, rolls, state
  deltas, active room participants (using `display_name`), known exits, and clues.
- **A2 Card-to-Scenario Converter**: convert the currently open RisuAI character card and lorebook
  into a SAGA scenario (using deterministic zero-LLM extraction, backend LLM with optional
  `reasoning_effort`/`custom_headers`/`custom_body`, or RisuAI's `submodel`) and bind it directly
  to the active chat. You can also use the standalone visual converter at `GET /converter` or
  `tools/saga-converter.html`.

## LLM configuration options

### 1. RisuAI's own models (Hook mode)
Use your existing RisuAI main model (and optional `submodel` with `use_risu_aux_intent=true`). No
extra API keys need to be given to SAGA.

### 2. Server-side OpenAI-compatible LLM (Provider mode or Hook backend intent)
Set `SAGA_LLM_BASE_URL`, `SAGA_LLM_API_KEY`, `SAGA_NARRATOR_MODEL`, and optionally
`SAGA_INTENT_MODEL` in SAGA's `.env` (see [`.env.example`](../../.env.example)). Works with
OpenRouter, Ollama, vLLM, and other OpenAI-compatible endpoints, with optional
`SAGA_LLM_REASONING_EFFORT`, `SAGA_LLM_CUSTOM_HEADERS`, and `SAGA_LLM_CUSTOM_BODY`.

### 3. Optional Vertex service-account JSON
Paste a Google Cloud service-account JSON into **Settings → Plugins → SAGA SRD Demo → Vertex
서비스 계정 JSON** (`vertex_service_account_json`). SAGA derives the project/global endpoint and
uses **Gemini 3.8 Flash** (`LOW` thinking) for bounded natural-language intent and post-commit
narration. Leaving it blank uses server-side LLM config (if set) or deterministic fallback.

#### Credential boundaries
PocketRisu stores plugin settings in its own save store (do not assume they are encrypted). The
plugin sends the Vertex JSON only to the configured SAGA server via `x-saga-vertex-secret`
(base64 transport encoding, not encryption; masked in request logs). Use localhost, a trusted
private network, or HTTPS. SAGA keeps credentials only in request memory—never in game state,
journals, snapshots, tool tapes, or error responses.

## Commands & actions

Commands are derived automatically from the world's pinned rulebook (`<action> <values...>
[name=value...] [advantage|disadvantage]`). A malformed command returns the full command list for
that rulebook.

Common SRD 5.2.1 commands:

| Command | Effect |
| --- | --- |
| `attack opponent` | Attack against the target, including damage on a hit (`advantage`/`disadvantage` supported) |
| `check strength 10` | Ability check against DC 10 |
| `save dexterity 12` | Saving throw against DC 12 |
| `move north` | Move along a room exit on the world map |
| `damage opponent 5` | GM-declared damage (HP stops at 0) |
| `heal player 3` | GM-declared healing up to max HP |
| `inflict opponent prone` | Add a named SRD condition |
| `cure opponent prone` | Remove a named condition |
| `wait 2 rounds` | Advance simulated time (`round`/`minute`/`hour`/`day`) |
| `give player torch 3` | Add exact item count (`[a-z0-9_]{1,40}`) |
| `take player torch 1` | Remove item count (rejected if fewer held) |

In addition, protocol v4 / M1–M3 support:

- **Pure RP turns (`narrative_turn`)**: ordinary dialogue/RP passes through cleanly without 422
  errors, committed as a state-neutral no-op event so rerolls stay consistent.
- **Grounded natural-language actions & free-action GM adjudication (`adjudicate_action`)**:
  creative actions beyond rigid commands can be resolved deterministically over the durable tape.
- **Map v2 & exploration**: locked exits (`unlock_exit`), loot (`take_loot`), room inspection &
  clues (`inspect_room`), NPC movement (`move_npc`), quest updates (`quest_update`), append-only
  GM corrections (`gm_correction`), and dynamic in-play `register_actor` / `add_room`.

## Editing, rerolling, and recovery

- **Regenerate (reroll)**: reuses the committed event, rolls, and state transition. Prose can
  change while HP, inventory, map position, and dice results stay identical.
- **New message**: sending the same text as a new user turn creates a new action.
- **Latest-turn edit**: changing the meaning of the latest user turn forks a new branch from its
  committed predecessor; the prior branch remains intact.
- **Earlier-turn edit**: rewind to the first changed turn and generate there. Hidden turns remain
  part of authoritative history; comments are excluded.
- **Lost response / restart**: regenerate the same user message to recover the committed result
  from SQLite without duplicating the action.
- **Upgrading legacy v1 chats**: existing v1 worlds remain read-only for exact original-history
  replay; start a new chat for newer protocol versions.

> **Scope note:** History per request is currently capped at 256 non-comment user turns. Exact game
> state lives in the SAGA SQLite database (`worlds.sqlite3`), not inside RisuAI chat backups.
