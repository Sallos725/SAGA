# ADR 0013: Card-to-Scenario Conversion & Dynamic Actor/Room Registration (A2)

- Status: Accepted
- Date: 2026-11-27 (Asia/Seoul)
- Related: [DIRECTION.md](../DIRECTION.md), [TRPG_FEATURE_REVIEW.md](../TRPG_FEATURE_REVIEW.md) (A2), [ADR 0011](0011-scenarios-and-free-action-adjudication.md), [ADR 0012](0012-exploration-gm-correction-and-cards.md)

## Context

SAGA separates **rulebook mechanics** from **scenario initial worlds** (ADR 0011), so players can pair any scenario with any compatible rulebook. However, most RisuAI/PocketRisu users already own third-party character cards (`.charx`, `.png`, CCv2/CCv3 `.json`) and lorebooks that lack explicit SAGA numbers or map graphs. Requiring manual JSON authoring before playing creates friction, and long sessions also introduce improvised NPCs and locations that must enter authoritative `WorldState` cleanly without duplicating existing NPCs under aliases.

At the same time, SAGA's core invariants forbid live-parsing arbitrary cards into `WorldState` during turns without validation or letting an LLM mutate `WorldState` directly.

## Decision

### 1. Explicit Draft-then-Confirm Conversion Pipeline (Offline / Backend / Plugin / Standalone HTML)

- Arbitrary RisuAI cards (`.charx`, `.png` `tEXt`/`zTXt`/`iTXt` `ccv3`/`chara`, CCv2/CCv3/PocketRisu `.json`) are converted **outside turn resolution** into a validated `ScenarioSpec` draft + RisuAI scenario module (`type: "risuModule"`).
- Every converted draft explicitly distinguishes field **provenance**:
  - `"card"`: extracted directly from card/lorebook text (names, aliases, explicit HP/locations).
  - `"inferred"`: rulebook default or LLM-estimated value.
  - `"gm_confirmed"`: explicitly reviewed/edited by the user.
- Four entry points share the exact `ScenarioSpec` v1 contract:
  1. **Deterministic zero-LLM parser** (`saga_trpg.rulebooks.converter.card_to_scenario_draft`)
  2. **CLI** (`python -m saga_trpg.rulebooks import-card`)
  3. **Backend API + Standalone Single-HTML WebApp** (`POST /v1/saga/convert` & `GET /converter` / `tools/saga-converter.html`)
  4. **PocketRisu Plugin v0.10.0 Panel** (converts the currently open character/lorebook, allows editing the draft, and saves the confirmed scenario per chat in `pluginStorage` under `saga:chat-scenarios:v1`)

### 2. Hash-Neutral Actor Metadata & Dynamic In-Play Registration Events

- `ActorState` adds three hash-neutral optional fields (`exclude_if` when unset/empty so existing worlds keep identical hashes):
  - `display_name: str | None`
  - `aliases: list[str]`
  - `provenance: dict[str, str]`
- Two reserved deterministic events allow registering newly introduced entities in play with full `StatePatch` audit trails:
  - `register_actor` (`RegisterActorPayload`): registers a new actor into `WorldState.actors`. Enforces **alias/name deduplication** against all existing actors' `actor_id`, `display_name`, and `aliases` (case-insensitive) and rejects conflicts unless `allow_distinct_instance=true` is explicitly set (for distinguishing multiple individuals sharing a name).
  - `add_room` (`AddRoomPayload`): registers a new `Room` connected to an existing room (with optional return exit, loot, and clues), respecting the 64-room cap and forbidding exit collisions.

### 3. Flexible OpenAI-Compatible LLM Parameters

- `OpenAICompatConfig` (used by narrator, intent classifier, and A2 LLM enrichment) adds strictly validated optional fields:
  - `reasoning_effort` (`none|minimal|low|medium|high|xhigh`)
  - `custom_headers` (rejects CRLF/NUL injection and hop-by-hop/forbidden headers like `Host`/`Cookie`; excluded from `repr`)
  - `custom_body` (rejects overriding reserved protocol keys `model`, `messages`, `stream`)
- Exposed via server env vars (`SAGA_LLM_REASONING_EFFORT`, `SAGA_LLM_CUSTOM_HEADERS`, `SAGA_LLM_CUSTOM_BODY`), CLI flags, `/v1/saga/convert`, PocketRisu plugin panel, and `tools/saga-converter.html`.

## Security & Invariant Guarantees

- Uploaded `.charx`/`.png`/`.json` bytes are parsed strictly in memory with magic-byte checks, 16 MB upload / 4 MB JSON size limits, ZIP path-traversal blocking, and bounded `zlib` decompression.
- Client-provided LLM endpoints on `/v1/saga/convert` require `https://` or loopback `http://` and block cloud metadata hosts (`169.254.169.254`).
- `GET /converter` injects a per-request cryptographic nonce CSP (`script-src 'nonce-...'`, no `'unsafe-inline'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Cache-Control: no-store`.
- `tools/saga-converter.html` and `saga_provider.js` use safe DOM APIs only (zero `innerHTML`, zero `localStorage` secret storage).
