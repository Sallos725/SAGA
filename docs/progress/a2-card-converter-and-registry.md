# A2: Card-to-Scenario Conversion, Dynamic Registry, and Flexible LLM Config

Date: 2026-11-27 (Asia/Seoul).
Status: implemented and verified offline on branch `Sallos725/a2-card-converter-and-registry`.
Contract: [ADR 0013](../adr/0013-card-conversion-and-dynamic-registry.md).

## Completed

- **Flexible OpenAI-Compatible LLM Config**: Added `reasoning_effort`, `custom_headers`
  (CRLF/hop-by-hop validated, redacted from `repr`), and `custom_body` (reserved-key guarded)
  to `OpenAICompatConfig`, `OpenAICompatNarrator`, `OpenAICompatIntentTool`, `api/app.py` env
  vars, CLI, standalone converter, and plugin UI.
- **Dynamic In-Play Registration (`register_actor`, `add_room`)**: Hash-neutral `ActorState`
  metadata (`display_name`, `aliases`, `provenance`); deterministic `register_actor` with
  alias/name deduplication (`allow_distinct_instance` override) and `add_room` with room-cap
  and exit-collision checks.
- **Multi-Format Card Converter (`converter.py`, CLI `import-card`, `POST /v1/saga/convert`)**:
  In-memory safe parsing of `.charx` (ZIP), `.png` (`tEXt`/`zTXt`/`iTXt`), and CCv2/CCv3/PocketRisu
  `.json`; zero-LLM deterministic baseline with `"card"` vs `"inferred"` provenance; optional
  LLM-assisted enrichment.
- **Standalone Single-HTML Converter (`tools/saga-converter.html`, `GET /converter`)**:
  Zero-CDN single HTML file usable via `file://` or FastAPI `/converter` (with per-request
  nonce CSP), drag-and-drop `.charx`/`.png`/`.json`, visual editor with provenance badges,
  offline/direct-LLM/backend modes, and `.scenario.json` / `.module.json` downloads.
- **PocketRisu Plugin v0.10.0 (`packs/risu-starter/saga_provider.js`)**:
  In-panel A2 converter for the currently open card/lorebook (backend rule / backend LLM with
  `reasoning_effort`+`custom_headers`+`custom_body` / RisuAI `submodel`), per-chat confirmed
  scenario persistence (`saga:chat-scenarios:v1`), and `display_name` on the B2 Scene Board.

## Verification

- `pytest`: 647 passed (including `tests/test_a2_converter_and_registry.py`)
- `node --test tests/risu_provider.test.cjs`: 37 passed
- `ruff check .`, `ruff format --check .`, `compileall`, `scripts/export_rulesets.py --check`: clean.
