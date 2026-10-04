# SAGA PocketRisu starter

An API-key-free command demo for PocketRisu **1.13.0**, plugin API **3.0**.
It uses the existing SRD 5.2.1 subset, deterministic local dice, committed facts as default
narration, and no semantic memory. Optional Vertex narration needs service-account JSON;
the default remains API-key-free. NMOS is not connected.
Plugin **0.3.0** uses unchanged bridge protocol/identity **v2**.

## Install

1. Start the isolated host/runtime using [the deployment guide](../../deploy/README.md).
2. Open PocketRisu on port **6011**. Import `saga_provider.js` under Settings → Plugins.
   Allow the plugin's provider/replacer permissions when the host asks.
3. Import `saga-srd-demo.card.json` as a character card.
4. Select the **SAGA SRD Demo** model (`pluginmodel:::SAGA SRD Demo`).
   Use it only as the main model; auxiliary generations are deliberately rejected.
5. Turn off automatic `*says nothing*` insertion in host settings (`useSayNothing=false`).
   This is required for the demo's Continue action to reject without contacting SAGA.
6. Open the card and send `attack opponent`.

This plugin is intended for the dedicated SAGA playtest host. Disable it when using another
main provider: the global main-request hook can otherwise add an opaque ticket line to that
provider's prompt. It sends no SAGA request unless the SAGA provider consumes the ticket.

The plugin's `server_url` defaults to `http://saga-runtime:4850`. PocketRisu routes that
address through its server with `networkRoute: local_network`; your browser need not resolve
Docker service names. The backend is not a public API and needs no API key in this isolated
setup. The `host_instance_id` setting may be left empty: a generated identity is retained in
host plugin storage. Keep that identity and the backend save volume when restarting or moving
the same installation. Use a different identity for an independent installation; changing it
starts a different campaign namespace.

### Upgrading an existing v1 chat

Upgrade the backend and plugin together; do not run old and new backends against the same
database concurrently. Existing v1 chat worlds are **read-only**: regenerating an exact original
user-message prefix returns its original event id and rolls. New actions and any unmatched raw
text, including cosmetic edits to old v1 messages, return 409. Restore the exact original history
to read it, or start a **new host chat** for v2 play. The adapter checks for the old world before
using the v2 namespace, so upgrading cannot silently restart an existing chat at genesis.
There is no database migration and no automatic transfer of a v1 campaign to v2.

## Optional Vertex narration

Open **설정 → 플러그인 → SAGA SRD Demo → Vertex 서비스 계정 JSON** and paste the entire
service-account JSON. Keep **SAGA SRD Demo** as the main model. SAGA reads `project_id` and
uses the global Vertex endpoint with **Gemini 3.8 Flash** automatically. No endpoint, model,
region, auxiliary-model or preset configuration is needed. Leave the field empty for the
existing deterministic fallback.

The input is `vertex_service_account_json`; expand the plugin's parameters if it is hidden.
A malformed JSON/key is rejected before any game action. A valid key with failed authentication,
permissions, model output or timeout falls back to recorded facts after the action commits,
with a short visible notice. Regenerate the same message to retry narration; the event, HP and
rolls remain unchanged. Two approved live Gemini calls on 6011 succeeded with unchanged
authoritative results. Actual billed cost was not observed; see the
[verification report](../../docs/progress/risu-vertex-narration.md).

The backend fixes the model to `gemini-3.8-flash`, thinking to `LOW` and output to 2,048 tokens.
[Google's model guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-flash)
documents the model and supported thinking levels. No natural-language intent or NMOS is added.

### Credential and narration boundaries

PocketRisu saves this input as a plugin setting; protect settings and exports containing it.
It is not claimed to be encrypted. The plugin sends it only to the configured SAGA server in
`x-saga-vertex-secret`, encoded as base64. Base64 is transport encoding, not encryption. The
pinned host masks that entire header in request logs; use the trusted private deployment or
HTTPS for credentials. SAGA keeps credentials/tokens in request memory, never its game state,
journal, snapshots, files or error responses. Uploaded token URLs cannot redirect authentication.
Existing saved host presets/key pools are not read or copied.

Only bounded text already present in the host prompt is sent for narration: eight system and
24 recent user/assistant messages, each capped at 4,000 characters. Ticket-bearing messages,
metadata and non-text content are omitted; hidden raw history is not rebuilt. The model also
receives committed facts, state, rolls and event id. Its prose is not parsed into state;
the separately appended **SAGA 확정 판정** block remains authoritative.

The backend has a 25-second narration budget, with bounded OAuth and Vertex HTTP calls. The
plugin waits up to 35 seconds for the whole response. Cancellation or changed chat/settings
suppresses stale output but cannot undo a committed action or guarantee cancellation at Google.
Regeneration can incur another narration request. Streaming, multimodal context, model tools
and general card/lore compatibility are not supported.

## Play

| Command | Effect |
| --- | --- |
| `attack opponent` | One player attack against the opponent, including damage on a hit |
| `check strength 10` | Ability check against DC 10 |
| `save dexterity 12` | Saving throw against DC 12 |
| `attack opponent advantage` | Attack with advantage |
| `check wisdom 15 disadvantage` | Ability check with disadvantage |

Abilities: `strength`, `dexterity`, `constitution`, `intelligence`, `wisdom`, `charisma`.
DC range: 1–100. Commands are case-insensitive; no slash prefix. One command per user message.
The player starts at HP 20, the opponent at HP 30/AC 13. See the
[ruleset scope and attribution](../../docs/RULESET_LICENSES.md) for the supported SRD subset.
There is no automatic enemy turn, natural-language interpretation, encounter reset command,
or complete combat simulation. A new host chat starts a new world.

The response shows recorded facts, authoritative HP, exact rolls and the event id.
**Regenerate** reuses the same event and rolls. Sending the same text as a **new user message**
is a new action. Default fallback text is deterministic; optional Vertex narration can change
the prose on regeneration while retaining the authoritative result.
For v2 chats, changing case, spacing or DC zero padding on the same message (for example
`check strength 10` → `CHECK  strength 010`) reuses its event and rolls. A genuinely different
validated command still creates an alternate branch.
**Continue** is unsupported and returns a clear error without a runtime request when the
required `useSayNothing=false` setting is applied. PocketRisu defaults this setting to true;
when enabled, an empty Continue inserts a synthetic `*says nothing*` user turn instead.
That placeholder reaches SAGA and is rejected with 422 before dice or state mutation, including
host retries. Remove the placeholder before the next command and disable the setting.

## Editing and recovery

- Changing the meaning of the latest v2 user command creates a branch from its committed predecessor. The old
  branch remains intact. Deleting the last action and regenerating an earlier turn reuses that
  earlier committed state.
- After editing/deleting an earlier turn in a v2 chat, rewind to the first changed user turn and
  generate there. The bridge rejects an uncommitted changed prefix instead of automatically
  executing every later action. Comment messages are excluded from the action history.
- Disabling messages or using **disable all before** only hides prompt context. Hidden user
  turns remain in the submitted history, and their committed events remain authoritative.
  A hidden turn without a matching committed prefix (including a never-executed draft) returns
  409; restore it for review, or remove the unused draft. Hidden turns are never executed
  automatically. Restore a hidden latest user turn before generating.
- After an unsupported command, edit or remove that user message before continuing.
- If a response is lost, regenerate the same user message. A completed event is recovered from
  SQLite even after a runtime restart. Do not send a duplicate new message to retry.
- If chat, swipe, command or plugin settings change in flight, the plugin suppresses the result.
  A committed result remains on its original immutable branch and can be recovered by returning
  to that history. Cancellation/timeouts cannot undo an already committed action.
- This first slice limits full non-comment history, including hidden turns, to 256 user turns.
  Every prefix gets a copied journal
  branch; storage grows quadratically with turn count. Long-horizon storage optimization is
  a later task, not a demonstrated capacity of this demo.

The card and plugin are presentation/transport artifacts. Exact state remains in the runtime
save volume; exported cards and host chat backups alone are not campaign backups.

This is a trusted solo-play demo, not adversarial benchmark isolation. A new message id is
intentionally a new action; editable client ids, true semantic edits and a known seed can still
be used to explore alternate outcomes. The v2 fixes prevent accidental cosmetic rerolls and
context-hiding resets, not every form of deliberate result selection.
