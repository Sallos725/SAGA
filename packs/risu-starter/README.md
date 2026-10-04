# SAGA PocketRisu starter

An API-key-free command demo for PocketRisu **1.13.0**, plugin API **3.0**.
It uses the existing SRD 5.2.1 subset, deterministic local dice, committed facts as narration,
and no semantic memory. It does not call an LLM or NMOS.

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
is a new action. The fallback text is deterministic, so regenerated prose normally stays the same.
**Continue** is unsupported and returns a clear error without a runtime request when the
required `useSayNothing=false` setting is applied. PocketRisu defaults this setting to true;
when enabled, an empty Continue inserts a synthetic `*says nothing*` user turn instead.
That placeholder reaches SAGA and is rejected with 422 before dice or state mutation, including
host retries. Remove the placeholder before the next command and disable the setting.

## Editing and recovery

- Editing the latest user command creates a branch from its committed predecessor. The old
  branch remains intact. Deleting the last action and regenerating an earlier turn reuses that
  earlier committed state.
- After editing/deleting an earlier turn, rewind the chat to the first changed user turn and
  generate there. The bridge rejects an uncommitted changed prefix instead of automatically
  executing every later action. Disabled/comment user turns are excluded from the active prefix.
- After an unsupported command, edit or remove that user message before continuing.
- If a response is lost, regenerate the same user message. A completed event is recovered from
  SQLite even after a runtime restart. Do not send a duplicate new message to retry.
- If chat, swipe, command or plugin settings change in flight, the plugin suppresses the result.
  A committed result remains on its original immutable branch and can be recovered by returning
  to that history. Cancellation/timeouts cannot undo an already committed action.
- This first slice limits active history to 256 user turns. Every prefix gets a copied journal
  branch; storage grows quadratically with turn count. Long-horizon storage optimization is
  a later task, not a demonstrated capacity of this demo.

The card and plugin are presentation/transport artifacts. Exact state remains in the runtime
save volume; exported cards and host chat backups alone are not campaign backups.
