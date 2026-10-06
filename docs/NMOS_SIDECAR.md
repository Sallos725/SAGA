# NMOS sidecar: what SAGA binds to

Read from the running sidecar's OpenAPI (`NMOS sidecar 0.3.0`, image
`ghcr.io/sallos725/nmos-sidecar:edge`) on 2026-10-06. This records the host's contract as
observed; SAGA does not vendor or reimplement any of it ([ADR 0001](adr/0001-state-vs-memory.md)).

## The sidecar's model

The sidecar remembers **conversations**. A host keeps it in sync by reconciling the chat's
message manifest and sending the message bodies it asks for; it then answers a query with a
packet of recalled text that it extracted (entities, facts, threads) in the background.

| Route | Purpose | Fields SAGA uses |
| --- | --- | --- |
| `POST /v1/sync/reconcile` | Declare the manifest of a chat | `host`, `chat_id`, `hash_version`, `messages[] {host_logical_id, revision_hash, role, name, swipe_count, special_comments}` → `needed_bodies[]` |
| `POST /v1/sync/bodies` | Send message bodies; may carry `then_reconcile` | `bodies[] {host_logical_id, revision_hash, content, metadata}` → `stored`, `rejected`, `reconcile` |
| `POST /v1/retrieve` | Recall for a query within a token budget | `host`, `chat_id`, `query`, `budget_tokens` → `trace_id`, `freshness`, `packet {text, token_estimate, excerpt_count}` |
| `GET /v1/health` | Liveness | — |

Other routes (canon sync, output, traces, conversations, entity links, repairs, config,
inspector) belong to the PocketRisu-facing plugin and the operator UI; SAGA does not call them.

## How SAGA maps onto it (`adapters/memory/nmos.py`)

- A world branch is a conversation: `chat_id = "<world_id>/<branch_id>"`, `host = "saga"`.
- A committed event is a message: `host_logical_id` = event id, `revision_hash` = sha256 of
  the committed facts text, `role = "char"`, `name = "SAGA"`. Only prose facts travel; exact
  HP, inventory and rolls never do.
- `remember` sends the new body together with `then_reconcile` carrying the branch's whole
  committed manifest (the bridge builds it from the journal, so the adapter is stateless and a
  sidecar that lost its data can ask for any body and get it in a second `bodies` call).
  Re-sending is idempotent by hash.
- `retrieve` sends the user's action text as the query with `budget_tokens = 120 × limit` and
  returns the packet text as one `MemoryHit` with `trace_id` and `freshness` in its metadata.
- Every failure is soft: a write logs and returns; a read logs and returns no hits. A
  2-second timeout bounds the cost of an absent sidecar.
- Actor-scoped (epistemic) retrieval is not offered by this sidecar version; `actor_scope`
  is ignored and documented as such. Hidden benchmark checks must therefore not be written
  to memory at all.

## Enabling it

Set `SAGA_NMOS_URL` (for example `http://nmos:8790` on the sidecar's network) on the SAGA
runtime; optional `SAGA_NMOS_API_KEY` (sent as a bearer token) and
`SAGA_NMOS_TIMEOUT_SECONDS`. Unset means `NullMemory`. The 6131 and 6011 deployments leave
it unset: pointing a playtest at a production sidecar would create `saga/...` conversations
in its dashboard, so that is an explicit operator decision.

## Not verified

The binding is tested against a fake sidecar that implements the routes above as the OpenAPI
describes them. A live round trip (reconcile, bodies, retrieve with a real packet) has not
been run; the first live run should check that `role = "char"` messages are extracted and
that the packet text is useful to the narrator.
