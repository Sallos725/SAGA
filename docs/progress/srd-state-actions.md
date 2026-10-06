# SRD 0.2.0 state actions and PocketRisu commands

Date: 2026-10-06 (Asia/Seoul).
Status: merged to `main` (`af8d101`, pull request 12) with a follow-up for fork cleanup
and documentation. Live on the 6131 test host; not deployed to the 6011 playtest runtime.

## Changed

- `srd-5.2.1@0.2.0` adds `damage`, `heal`, `apply_condition`, `remove_condition` and
  `advance_time`. `0.1.0` stays registered with its exact action set; both share one rules
  class gated by version. See [ADR 0006](../adr/0006-ruleset-versions-and-state-actions.md).
- The PocketRisu bridge creates new worlds on the latest SRD version and leaves existing
  worlds pinned. New commands: `damage <target> <n>`, `heal <target> <n>`,
  `inflict <target> <condition>`, `cure <target> <condition>`,
  `wait <n> <round|minute|hour|day>`. Existing command identities are unchanged.
- `ActorState.conditions` serializes sorted. State hashes were already order-independent;
  responses and stored JSON now are too.
- A fork created for a divergent edit is discarded again when the rules reject that edit
  before any commit. `SQLiteWorldRepository.discard_empty_branch` refuses `main` and any branch
  with its own entries, tool records or child branches. A racing identical request that finds
  its selected fork gone reselects and gets the same rule error, not a 404.
- Packs export the latest version with sample state events. The starter card, plugin help
  text and READMEs list the new commands.
- `deploy/compose.risu-6131.yaml` runs a backend-only runtime on `127.0.0.1:4852` for an
  existing host-network PocketRisu on port 6131, with its own data volume.

## Follow-up: `0.3.0` inventory and the status footer (same day)

- `srd-5.2.1@0.3.0` adds `give` and `take`: exact per-actor counts, keys `[a-z0-9_]{1,40}`,
  no effects, over-take rejected before commit, key removed at zero. New bridge worlds pin
  `0.3.0`; `0.2.0` worlds keep rejecting the new kinds. Bridge commands
  `give <target> <item> <n>` / `take <target> <item> <n>`.
- Plugin 0.5.0 renders one compact HTML footer (HP, conditions, time, inventory, rolls,
  turn, event id) from the response; verified rendering on the 6131 host.

## Verification

- Offline: `pytest` 290 passed; `ruff check`/`format --check` clean; `node --test` 22 passed;
  `scripts/export_rulesets.py --check` and schema exports unchanged by the serializer.
- Live, 6131 host (PocketRisu 1.13.0, same image digest as 6011, Safari client), backend
  built from this branch: `damage opponent 5` → `inflict opponent prone` → `wait 2 rounds` →
  `attack opponent` committed as four entries on `main` of one `0.2.0` world. The attack was
  the only roll. Replay from genesis equals the stored state (opponent 25/30, `prone`,
  simulated time 12 s). The tool tape is empty because Vertex was not configured.
- The runtime was rebuilt once after the serializer fix; the played world replayed unchanged.

## Findings

- With `host_instance_id` empty, the plugin waited forever on `api.pluginStorage` on this
  host and never sent a request. Setting any explicit id skips that path and worked
  immediately. The cause inside the host was not identified; the starter guide now
  recommends an explicit id. The 6011 playtest did not show this.
- Copilot review flagged the set-ordering issue and three sentences that still said the bridge
  pinned `0.1.0`; both were fixed before merge.
- A rejected divergent edit had left an empty fork named after the rejected event id. It
  was harmless for replay but accumulated per failed edit; fixed in the follow-up.

## Unresolved

- Conditions have no mechanical effect on rolls and no duration. Death saves,
  unconsciousness at 0 HP, resistances, item effects, equipment and currency are still
  unimplemented.
- The Vertex natural-language interpreter still proposes roll actions only; state changes
  need commands.
- The 6011 runtime still runs the pre-`0.2.0` image. Upgrading it keeps existing chats on
  `0.1.0`; only new chats gain the state commands.

## Next recommended task

Condition effects on rolls, but as ruleset data (a condition-to-advantage table the engine
interprets) rather than more Python branches: the owner intends rulebooks to arrive as RisuAI
modules on top of a generic SAGA core. Decide first whether modules carry data only or also
sandboxed scripts (see the open question in the handoff).
