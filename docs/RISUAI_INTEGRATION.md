# RisuAI Integration Notes

## Current implementation: no-memory action demo

The [starter](../packs/risu-starter/README.md) implements a small playable path on PocketRisu
1.13.0: v3 provider → `POST /v1/chat/completions` → validated SRD `GameEvent` → persistent
repository → fallback or optional Vertex narration → authoritative status. It uses
deterministic commands, optional bounded natural-language intent, local seeded dice and no
memory provider. Streaming, arbitrary card/lore integration and a complete ruleset remain open.
Plugin 0.7.0 and protocol 3 are current: the plugin forwards an enabled module's rulebook,
commands derive from the world's rulebook and the footer prints `saga.display`. Backend/plugin
0.4.0 remain deployed to 6011 (one natural attack and one regeneration passed live there; see
the [live report](progress/risu-natural-intent-live.md)); the 6131 test host runs `main` and
`scripts/smoke_plugin.cjs` exercises the plugin path there without the UI.

The current installation uses the **SAGA SRD Demo** plugin provider, rather than configuring
an ordinary Custom API URL: stable host identities and a main-generation guard are required.
The desired transparent-card experience below remains a future target.

### Observed host contract and request guard

The pinned image is recorded in [deployment instructions](../deploy/README.md). Its distributed
source maps were inspected for `apiV3/v3.svelte.ts`, `process/request/request.ts`,
`process/index.svelte.ts`, `DefaultChatScreen.svelte`, `plugins.svelte.ts`, `characterCards.ts`
and `localNetwork.ts`:

- `getCharacter()` returns the selected character with `chats[chatPage]`. `chat.id` identifies
  the chat; `message.chatId` identifies a message. The current host's
  `getCurrentCharacterIndex()` returns a string character id, whereas `getChatFromIndex()`
  resolves a numeric array index. The new plugin uses `getCharacter()` to avoid that mismatch.
- The `beforeRequest(prompt, mode)` replacer receives the real request mode. The v3 provider
  callback receives `mode: v3`, because the host overwrites the original mode.
- Main send and regeneration leave the current active message as `user`; the host removes
  the previous assistant before regenerating. With the required demo setting
  `useSayNothing=false`, continuation retains the assistant. The plugin requires mode `model`,
  a matching prompt user message, and a final active user message. PocketRisu defaults
  `useSayNothing` to true: an empty Continue then appends `*says nothing*` as a user message.
  That path reaches the backend but is rejected by the closed interpreter with 422, with no
  dice or state mutation. Host retries do not change that outcome. Remove the placeholder
  and disable insertion before resuming commands.
- That hook adds an opaque, single-use, in-memory ticket to the prompt. Only this plugin's
  provider can consume the ticket. Stable identities and raw user commands then travel in
  JSON metadata. Tickets are never sent to SAGA; when Vertex narration is enabled, bounded
  card/system/dialogue text is sent separately as narration context.
  Auxiliary calls have no ticket. Continuation with `useSayNothing=false` is explicitly
  rejected before networking; the enabled synthetic-user path instead fails closed at the API.
- `nativeFetch` with `networkRoute: local_network` routes through PocketRisu's server. Its
  local-network classifier accepts single-label Docker service names such as `saga-runtime`.
- Before and after I/O, the plugin verifies the selected character/chat, full message
  snapshot and server/host/credential settings. Stale responses are suppressed. The unload handler
  clears tickets and removes the replacer. Provider lifecycle cleanup belongs to the host.

These observations apply to the pinned PocketRisu build, not every upstream RisuAI version.

### Natural-language intent in plugin 0.4.0

The same Vertex JSON field enables a separate structured interpretation before game commit.
Supported scope is one explicit attack/check/save for player; unknown targets, missing DCs,
proficiency, modifiers, raw state/damage and extra proposal fields are rejected. For example,
`상대를 공격한다`, `힘으로 난이도 10 판정을 한다`, or `Make a dexterity saving throw, DC 12`.
Target aliases are opponent/상대/상대방/적, drawn from the committed roster. A check/save needs
an explicit ability and `DC`/`난이도` integer. The leading words attack/check/save still select
the strict command syntax, so malformed commands do not trigger paid repair attempts.

Protocol 3 keeps the v2 world namespace and exact canonical command hashes. Natural action
ids use `saga-risu-action-v3` and tagged raw text plus parent/message ids; no LLM is needed
to derive or replay them. Natural text edits intentionally create a new action even for
whitespace changes. Hidden natural turns require a committed prefix, and v1 history remains
exact-history read-only. Upgrading a v2 chat needs no reset or storage migration.

The existing generic tool tape durably claims interpretation before the remote attempt and
stores its validated proposal/refusal before dispatch. It is anchored to main plus event id,
with immutable raw action/roster arguments and a verified parent-state hash. Branch reselection,
changed assistant prose and restart reuse the same result. Definite refusal returns generic
422 with no event/dice; timeout or ambiguous execution stays pending and returns 503 without
another interpretation attempt. A refusal can leave an empty initialized world and safe tape
record. Raw action text is retained; credentials, tokens and rejected model output are not.
No second cache/store was introduced, and committed-event replay already avoided interpretation.

New accepted natural actions make at most one intent and one narration generation; commands
and committed replays make zero intent calls and at most one narration call. Intent uses a
25-second provider/27-second tape wait; narration retains 25 seconds, with 65/67-second plugin
proxy/wait budgets. Blank credentials preserve command fallback and allow committed natural
replay. Live Gemini verification is limited to `상대를 공격한다` and one regeneration;
checks/saves and other language decisions remain unverified live. See the
[natural-intent report](progress/risu-natural-intent.md) for strict grounding, pending failures,
reference reuse and full offline evidence. All agent-run paid tests still require prior approval.

### Optional Vertex narration in plugin 0.3.0

Paste service-account JSON into the plugin's **Vertex 서비스 계정 JSON** field; keep SAGA as
main. Blank input retains deterministic fallback. The plugin does not use PocketRisu's model,
preset or auxiliary APIs and makes no PocketRisu source/API/image change. See the
[one-field setup](../packs/risu-starter/README.md#optional-vertex-narration).

The completion contract adds optional `narration` metadata:

```json
{"narration": {"schema_version": 1, "provider": "vertex", "context": ["user: attack opponent"]}}
```

It must be paired with `x-saga-vertex-secret`: base64-encoded UTF-8 service-account JSON.
The credential is deliberately absent from the JSON body because the pinned host redacts this
entire secret header in its request logs. Base64 is not encryption. Missing/conflicting options,
malformed encoding/JSON, invalid account shape/key, and non-Google token URLs fail before dispatch.
The header is capped at 24,000 characters; decoded JSON at 16,000. `SecretStr` protects internal
representations. Completion validation errors omit supplied inputs, and narrator failure logs
omit exception tracebacks/provider text. Other API routes retain their existing validation behavior.
Credentials remain request-scoped and never enter an event, state, journal, snapshot or narration
prompt. PocketRisu itself retains the user-entered plugin setting; this is not encrypted-secret
storage and exports/settings must be protected. Existing saved credentials are not inspected.

The thin `VertexNarrator` uses standard `google-auth` with a service-account key. It fixes OAuth
to `https://oauth2.googleapis.com/token`, derives the project path from validated `project_id`,
and fixes inference to the global Vertex endpoint and `gemini-3.8-flash`. No uploaded endpoint,
universe, delegation or quota override configures transport. Only after commit or exact replay
does it obtain a token and call `generateContent`, with LOW thinking, no thought output, a
2,048-token cap, no model tools and no streaming. The API extra declares
`google-auth[requests,cryptography]`; the runtime/domain/storage dependencies are unchanged.

Context is bounded to the first eight textual system messages and the latest 24 textual
user/assistant messages, each including a role prefix within 4,000 characters. Ticket-bearing
messages, other content types and metadata are removed. Hidden raw history is never reconstructed.
Committed facts, state, rolls, resolution and event id are added separately as read-only JSON.
The model's prose never updates state; the plugin appends authoritative status from SAGA's result.

Empty/malformed output, authentication/provider failure or timeout preserves the committed
fallback plus a generic visible notice. Only non-thought text up to 12,000 characters is accepted.
Narration has a 25-second overall budget, 5-second OAuth transport timeout and 15-second Vertex
HTTP timeout. Plugin 0.4.0 uses a 65-second proxy timeout and 67-second wait to cover interpretation
plus narration and return fallback within the normal path.
No inference retries are added; standard OAuth refresh may retry transient token failures within
the outer wait budget. A worker-thread OAuth call or Google inference may finish after local
cancellation; no completed game action is rolled back. Retrying the same message replays the event
before attempting fresh narration. Existing stale-selection/message/configuration/unload guards
suppress delivery after changes. Command identity v2 and runtime/storage schemas remain unchanged; protocol 3 adds only
the explicitly tagged natural-action identity described above.

Automated tests use simulated OAuth/Vertex transports, including real SDK signing with an
in-memory synthetic key. Separately, two explicitly approved live Gemini calls through 6011
both returned 200 and displayed fresh prose with unchanged event/state/rolls/journal. Actual
billed cost was not observed. See the [narration report](progress/risu-vertex-narration.md)
for that bounded live verification.

### HTTP contract v3 (v2 commands remain supported; legacy v1 is read-only)

`GET /v1/models` lists `saga-srd-demo`. The non-streaming OpenAI-shaped completion request is
closed and described by [JSON Schema](../schemas/risu-completion.schema.json):

```json
{
  "model": "saga-srd-demo",
  "stream": false,
  "messages": [{"role": "user", "content": "attack opponent"}],
  "saga": {
    "schema_version": 3,
    "host_instance_id": "my-pocketrisu-installation",
    "chat_id": "host-chat-uuid",
    "actor_id": "player",
    "purpose": "action",
    "actions": [{"message_id": "host-user-message-uuid", "text": "attack opponent", "context_hidden": false}]
  }
}
```

`actions` is the complete user-message prefix, excluding only comments. Disabled and
`allBefore` user turns remain present with `context_hidden: true`. Assistant
prose never identifies an action. All ids must be present and user ids unique; the last prompt
message must match the latest action text exactly. `stream: true`, auxiliary purposes, extra
fields, other actors/models and missing metadata are rejected with 422 before dispatch. This
is a narrow OpenAI-compatible response/request subset, not a general-purpose model proxy.

The response contains normal `id/object/created/model/choices` fields (`created: 0` denotes
no timestamp), plus `saga` with `schema_version` matching the request, `identity_version`,
`legacy_read_only`, `world_id`, `branch_id`, `event_id`, `seq`,
`replayed`, `state`, `facts`, `rolls`, `patches`, `trace` and `resolution`. The plugin renders
HP and rolls directly from this result. It does not parse generated prose back into state.

### Persistent identity and immutable history

`stable_value_hash` uses canonical JSON and SHA-256. Namespace `saga-risu-v2` plus host
instance/chat ids derives the world id. Namespace `saga-risu-action-v2` plus world id,
parent event id, user-message id and the validated `{kind, actor_id, payload}` command derives
the event id. Canonical payloads include explicit ruleset defaults. Because each id includes
its parent, one event id identifies its whole action prefix. The response `branch_id` names
the branch that committed the action: `main` for an unedited chat, or the id of the first
divergent action for an alternate line. The actor is fixed to `player`; the ruleset is
`srd-5.2.1`, pinned per world at creation: new worlds take the latest registered version
(`SRD_LATEST` in the catalog, `0.6.0` at this revision, served from the data rulebook; new
chats therefore start on a generated room-graph map, see
[ADR 0009](adr/0009-locations-and-map-movement.md)); worlds
created on an earlier version keep that version, its exact action set and semantics, and
reject newer commands with a validation error. Adding command verbs never changes existing
command identities.
Every response carries `saga.display` — actor, record and world lines rendered from the
rulebook's `display` declaration — which plugin 0.7.0 prints in its footer without
interpreting them; older plugins ignore the field and older backends' responses fall back to
HP/conditions/inventory in the plugin.

Commands are parsed by the world's rulebook (`saga_trpg.rulebooks.commands`): the action name
or an alias, positional values in input order, `name=value`, quoted text, and the advantage
flags for roll-twice checks. Closed choices are lower-cased; free text keeps its case. Worlds
pinned to hand-written SRD versions use a frozen `srd-5.2.1@0.5.0` grammar (`LEGACY_SRD_SPEC`),
whose payload shapes match and whose verbs never change, so a verb added to a later SRD (such
as `move` in `0.6.0`) cannot change the identity of an action they already committed.
The turn may carry `rulebook: { content }`; it is validated and registered, and pins the
world only when the world is created. Natural-language interpretation stays limited to the SRD
demo rulebook.

If the rules reject a divergent edit before any commit, the fork created for it is discarded
again, so the host can retry with a corrected edit on the same message without leftover
branches. Set `host_instance_id` explicitly; see the starter guide for the storage hang
observed on one host when it was empty.

The synchronous closed-command parser normalizes case, whitespace and numeric DC formatting
before hashing. For the same message id and ancestry, `attack opponent`, `Attack  opponent`
and ` ATTACK opponent ` share an identity; so do `check strength 10` and `CHECK strength 010`.
This pure canonicalization does not call the injected async `IntentPort`. Committed entries
are retrieved before that port is invoked; on a new action its proposal must agree with the
canonical kind, actor and validated payload. No stochastic interpreter determines an identity.

Before resolving v2, the adapter looks up the legacy `saga-risu-v1` world for the same host/chat.
If it exists, v1, v2 and v3 requests may **only replay an exact original raw-text v1 prefix**.
The original event ids, state and rolls are returned with `identity_version: 1` and
`legacy_read_only: true`. Any unmatched prefix, new action, cosmetic raw-text edit or unknown
truncation returns 409, without creating a v2 world. V1 requests cannot create worlds or actions.
Legacy journals do not contain recoverable original host metadata; the adapter does not guess
that metadata or normalize old hashes. Restore exact old history for reading, or create a new
host chat for v2 play. Upgrade plugin/backend together and stop old writers; no automatic
migration, game-state metadata flag or database schema change is involved.

A chat continues on one branch. A new action is appended to the branch whose head is its
parent event, starting with `main`; consecutive turns never create branches. Only when no
branch ends at the parent (the host history diverged from what was committed) does the bridge
fork that parent prefix into a branch named after the new action, and that line then continues
in place too. No mapping table or storage migration is added. A request first locates the
committed event by id and verifies its branch prefix against the submitted identities, before
interpreting intent. Regeneration and restart therefore perform neither intent reinterpretation
nor dice execution. New message ids create new actions even when text repeats.

Explicit event ids are scoped per branch, so a branch that merely contains the parent id
is not trusted. The branch appended to, or forked from, must hold exactly the submitted parent
prefix; a branch that reaches the same id through other history is skipped, and if no branch
holds the prefix the request returns 409 before dispatch.

Each new commit is guarded inside its repository write transaction: the branch head must
still be the parent, and the event id must not exist on another branch of the world. A fork
is refused in its own transaction when the action is already committed, so a request that read
just before an identical one committed leaves no empty branch. Racing
requests therefore converge on one record or separate branches; the bridge reselects after a
conflict and never commits one action twice. An empty fork is reserved for the action it is
named after, so an interrupted fork is reused by its retry. A branch reserved by another
action's unresolved external tool is never selected.

Worlds written by the earlier per-turn layout (one copied branch per action) remain valid:
their events are located the same way, and the next action is appended to the latest of those
branches without creating another.

In v2, a genuine semantic edit to the latest action creates an alternate branch from its known parent.
Deleting the latest action reads the retained earlier prefix of the same branch. A changed earlier prefix that
has not itself been committed returns 409; rewind to the first changed turn and generate there.
The bridge never silently reruns every remaining turn. A located event whose branch prefix
differs from the submitted identities produces 409. Entries after the located event are not
part of that history; an action that does not follow the branch head forks from its parent. Hidden user turns do not disappear from the chain: each must match a committed
prefix or the request returns 409 before any write. A never-executed hidden draft is not treated
as an event; restore it for review or remove the unused draft. A hidden latest action returns
422 before any world/fork write. Visibility flags themselves are not part of event identity.

New message ids intentionally remain new actions. Client-controlled ids, genuine semantic edits
and the known seed still permit deliberate outcome selection; this is not adversarial benchmark
isolation. Journal storage is linear in an unedited chat; each divergent edit copies its
prefix once. Requests still carry and verify the whole action history, and each hidden turn
adds its own prefix read (quadratic in the worst case), so they remain bounded
to 256 non-comment user actions, including hidden turns. Longer campaigns need incremental
prefix verification and paginated history reads, without weakening the current replay contract.

### Failures and verification boundaries

Malformed commands/intents fail validation before dice. Unknown actor targets fail ruleset
validation before dice. Repository/disk failures use the existing 503 handling. No successful
completion is returned for an uncommitted event. Narration begins only after commit and receives
a deep copy; exceptions or empty narration fall back to immutable recorded facts. Cancelled or
timed-out transport may have committed, so retry the **same host message identity**. Ambiguous
external tool claims retain the existing durable-tape policy; this deployment uses local dice.

Run `pytest tests/test_risu_api.py` and `node --test tests/risu_provider.test.cjs`. The API suite
checks ten regenerations, zero extra dice and interpretation, restart, edits/deletions, concurrent
duplicates, pre-dispatch interruption, malformed identities/intents, isolation and narrator failure.
The Node suite exercises the plugin protocol/lifecycle with a host API harness, including
stale/inflight selection and auxiliary/continuation rejection. A harness is not a browser UI test;
live host verification is reported separately in [the integration progress report](progress/risu-command-demo.md).

## Target UX

Ideal user path:

1. Start backend (`docker compose up -d`).
2. Point RisuAI Custom API at the backend.
3. Import/use an existing or bundled card.
4. Play normally.
5. Runtime tracks exact game state; NMOS supplies long-horizon recall.
6. Regenerate prose without double-applying game effects.

## Adapter responsibilities

The RisuAI adapter is responsible for translating chat protocol semantics into runtime semantics.

It must determine:

- world/campaign id
- player/actor id
- latest user action
- whether this request is a new action or narration regeneration
- which committed event should be narrated

It must not:

- calculate combat rules in HTTP route code
- update HP from model-generated status windows
- re-roll because the user regenerated text
- expose hidden actor memories to the wrong narrator

## Host identity reference: oneuldo memory pilot

Reviewed on 2026-10-04: `Sallos725/oneuldo`, branch `codex/memory-pilot-6010`,
commit [`b6eb2ff90b6cff2110f77925fde2c5504b4c0086`](https://github.com/Sallos725/oneuldo/tree/b6eb2ff90b6cff2110f77925fde2c5504b4c0086).
This provides a concrete plugin-assisted route to turn identity even when the
OpenAI-compatible request itself lacks host ids.

The [plugin](https://github.com/Sallos725/oneuldo/blob/b6eb2ff90b6cff2110f77925fde2c5504b4c0086/card/plugins/oneuldo_memory_pilot.js)
reads the selected chat with `getChatFromIndex`. Its snapshot preserves `chat.id`
as the conversation id and each `message.chatId` as that message's id, together
with current text, role, disabled status, and comment status. It rejects missing ids.
`beforeRequest` synchronizes before the main model request, and the `output` hook
synchronizes after generation. It checks for chat/swipe changes during asynchronous
work and avoids injecting a result for a changed snapshot.

The pilot's server uses a full-snapshot SHA-256 revision and `expectedVersion` for
idempotent synchronization and conflicting-write rejection. The injected model packet
contains chat id, revision, and filtered knowledge; it does not already provide a
SAGA action id or dispatch SAGA events.

### Applying this to SAGA

- Resolve the host instance and chat id to a persistent SAGA world/branch mapping.
  Identify an action by its user-message id within the mapped history, retaining
  input content/version and the authoritative parent event to detect edits.
- Reuse the same committed event for an unchanged user action on regeneration or
  retry. A new message id is a new action even if its text repeats. An edited action
  or changed authoritative prefix needs a branch from the matching committed prefix.
- Do not use the pilot's whole-snapshot revision as the action id: it includes
  assistant text and therefore changes during narration regeneration. Its revision
  contract is useful for synchronization, separately from action idempotency.
- Use the host hooks to send explicit turn metadata to outer orchestration. Keep
  game-event validation and rule execution in SAGA. The pilot rebuilds NPC knowledge
  from selected assistant tags; SAGA cannot use regenerated prose to rewrite an
  already committed roll, damage result, or rule-relevant knowledge event.
- Retain semantic memory through `MemoryPort`; importing the pilot's separate
  memory store is unnecessary. Its memory failure can omit context, whereas a failed
  SAGA dispatch must not be presented as a successfully resolved game action.

### Evidence and remaining work

At the pinned revision, independently ran `npm run build` followed by
`node --test dist/tests/pilot.test.js dist/tests/pilot-plugin.test.js dist/tests/pilot-remote.test.js`
from `mcp/` under Node 22.23.3: **5 passed, 0 failed**. This covers the shipped plugin
in a VM with a real local HTTP server, revision conflicts, regeneration/swipe/edit/delete
memory projection, and persistence across a new process. It does not run a RisuAI UI.

The upstream [verification report](https://github.com/Sallos725/oneuldo/blob/b6eb2ff90b6cff2110f77925fde2c5504b4c0086/docs/notes/MEMORY_PILOT_VERIFICATION.md)
records PocketRisu 1.13.0 UI tests on an isolated host with a deterministic model.
The current plugin and six checked server files match the recorded source hashes.
The [6010 setup report](https://github.com/Sallos725/oneuldo/blob/b6eb2ff90b6cff2110f77925fde2c5504b4c0086/docs/notes/MEMORY_PLAYTEST_6010.md)
separately records installation and manual synchronization; actual PM-provider model
generation was not part of that installation verification. Neither host was accessed
or modified during this review.

That historical review led to the current plugin-to-SAGA mapping described above. Its actual
host send/regeneration, edited-input, repeated-text/new-message and restart checks are recorded
in the progress report. Continuation also requires the `useSayNothing` qualification above;
the original pilot evidence did not establish that setting-sensitive command behavior.

## Card compatibility strategy

Prefer a compatibility ladder:

1. Existing card, no modification: runtime acts as transparent orchestration layer where possible.
2. Optional small metadata block: campaign/ruleset/module hints.
3. Full runtime-aware card: explicit stats, IDs, module hooks, status UI.

Do not require every card to be rewritten just to test the engine.

## Status display

Generate status from authoritative state after commit. If injected into model context, label it as read-only structured state. Prefer UI/tool surfaces over asking the model to reprint numbers faithfully.

## Regeneration test

Mandatory acceptance test:

- Submit one attack action.
- Capture event id, roll records, target HP, journal length.
- Request 10 narration regenerations.
- Assert all four values are unchanged.
- Prose may differ.
