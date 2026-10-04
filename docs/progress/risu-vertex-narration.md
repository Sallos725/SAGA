# Optional Vertex narration

Date: 2026-10-04. Base: `040c293` on `codex/p1-vertex-narration`.

## Implemented behavior

In **설정 → 플러그인 → SAGA SRD Demo → Vertex 서비스 계정 JSON**, paste the complete
service-account JSON and keep **SAGA SRD Demo** as main. Starter/plugin 0.3.0 derives the
project/global Vertex endpoint and uses **Gemini 3.8 Flash**. No endpoint, model, region,
auxiliary-model or preset configuration is needed. Blank input keeps deterministic fallback.
No PocketRisu source, API or image change is part of this feature.

The plugin sends the JSON in `x-saga-vertex-secret`, using base64 solely as transport encoding.
The pinned host masks this entire header in request logs. The optional, versioned `narration`
body contains only bounded context. SAGA decodes into `SecretStr`, validates account shape and
private key before dispatch, and creates a request-scoped `VertexNarrator`. Standard
`google-auth[requests,cryptography]` handles authentication. OAuth and inference use fixed Google
endpoints; uploaded token URLs cannot choose a destination. Credentials never enter a game
event, state, journal, snapshot, narration prompt or error response. PocketRisu retains the
user-entered plugin setting; encryption is not claimed and settings/exports need protection.

OAuth and model calls occur only after commit or exact replay. Narration receives bounded
text already present in the host prompt plus read-only committed facts/state/rolls. Tickets,
metadata and non-text content are excluded; hidden raw history is not reconstructed. The
model never updates state. Its prose is followed by the existing authoritative status block.
Failures, empty/malformed output or the 25-second narration budget return recorded fallback
with a generic notice. The plugin allows 35 seconds for the completion response. Stale chat,
message, credential-setting, cancellation and unload guards still suppress delivery; a committed
action remains recoverable. Google work may outlive local cancellation. See the
[integration contract](../RISUAI_INTEGRATION.md) and
[starter limits](../../packs/risu-starter/README.md#optional-vertex-narration).

## Verification

- Red phase: **15 failed, 4 passed** against the previous API, including validation-input
  disclosure, missing Vertex behavior and fail-soft cases. The final focused Vertex suite
  has **28 passing tests**.
- Full Python suite: **202 passed**, with one existing Starlette/httpx deprecation warning.
  Node tests execute the shipped plugin: **21 passed**. Ruff check/format, compileall and
  whitespace checks passed.
- Real Google SDK boundary: an in-memory synthetic RSA key exercises credential construction,
  JWT signing and verification, token refresh through a mocked HTTP transport, then mocked
  `generateContent`. Tests cover standard iam/developer/appspot account domains, fixed endpoint
  and model, malformed header/JSON/key, missing options, non-Google token URI refusal, bounded
  timeout, auth/provider failure and absence of key/token markers in logs, responses and SQLite.
- Shipped plugin → real FastAPI/SQLite → real `VertexNarrator`/Google SDK harness, with synthetic
  credentials and mocked OAuth/Vertex transports: one attack and ten regenerations produced
  11 distinct prose variants with the same event, state, rolls and journal. Model callbacks
  observed the stored event first. Initial execution made two dice calls and one intent call;
  regeneration and postcommit model failure added none. Reopening the repository and replaying
  made zero dice and intent calls. Database files contained no synthetic key/token material.
- Supervisor checked the actual pinned host's request-log writer with a synthetic secret
  header: it was fully masked before SQLite persistence and after reopen. No real credential
  or host code change was used for that check.
- Self-review: network/auth behavior stays in an outer narrator adapter. Domain/runtime/storage
  and their identity semantics are unchanged; the completion request has only an optional typed
  extension. Existing bridge v2/legacy-read-only, command scope and capacity limits still apply.

## Live deployment on 6011

Runtime image `sha256:b83388fe6746bd6c849bf6c8b9fdbbda5c70b88f2a93e802b2ad4ab16c2e82ea`
and plugin 0.3.0 (`e7f3c62cc0bb72414ee2835c2fbefeda48cd202fc5f129096bb7219c97883bdf`)
were installed and verified. The official plugin import preserved the main/submodel settings,
host/server arguments and prior chat ids. The actual multiline JSON setting was confirmed.
Submitting synthetic invalid JSON in a new test chat displayed a safe 422 error without the
input; every pre-existing database row remained identical to the predeployment backup. The
actual host request log stored `x-saga-vertex-secret` as `[REDACTED]`, with neither raw nor
encoded synthetic input present.

With the field blank, an actual UI send and regeneration reused the same event, state, rolls
and journal containing one committed entry; the response displayed `기록 재사용`. The field
was restored blank for user input. No real credential or Gemini call was used in these checks.

The user subsequently entered their JSON and explicitly approved **at most two** paid generation
requests, with a stop on the first failure. Exactly two actual Gemini calls were then made via
UI regeneration in test Chat 6. Both returned HTTP 200 and displayed genuine narration (47 and
79 characters) followed by authoritative status. Event, state, rolls and the one-entry journal
remained identical. The disclosed estimate was **USD 0.03–0.06**, assuming at most 10,000 input
and 2,048 billed output tokens per call; this was an estimate, not a hard cost cap. Actual billed
cost was not observed. The two-call allowance is exhausted; no further paid calls are authorized.

Before those calls, a published-secret audit at `ef083a39c537dc0205e50b6935e7f03797fa7949`
covered all 138 tracked files plus PR #8's diff, body and comments. Comparison with the actual
key stayed inside the browser and found zero credential matches or complete private-key blocks;
no secret value was exported. This audit applies to that recorded revision.

Local evidence is under `/tmp/saga-risu-vertex-evidence/`: `json-red.txt`, `json-green.txt`,
`json-pytest.txt`, `json-node-green.txt`, `json-ruff-check.txt`, `json-ruff-format.txt`,
`worker-api-harness.json`, `worker-plugin-api.cjs`, `worker-api-server.py`,
`host-secret-masking.json`, `plugin-installed.json`, `live-invalid-json.json`,
`live-json-rerender.json`, `live-vertex-approved.json`, `published-secret-audit.json`,
`json-product-hashes.json`, `source-hashes.json` and
`worker-ledger.json`. These are local artifacts, not portable CI attachments.

Live Gemini verification is limited to the two approved calls above; it does not establish
long-run reliability or exact billed cost. Natural-language intent, NMOS, streaming, model
tools and general card/lore compatibility remain outside this slice. Future billable tests
follow the explicit scope/cost approval rule in [the handoff](../AGENT_HANDOFF.md).
