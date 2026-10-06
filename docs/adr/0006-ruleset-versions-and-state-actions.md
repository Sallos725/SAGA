# ADR 0006: Ruleset versions pinned per world, and dice-free state actions

Status: accepted (2026-10-06)

## Problem

The SRD 5.2.1 example only resolved rolls: checks, saves and one weapon attack. Nothing could
change hit points without an attack, record a condition, or move time. Adding those actions
raised two questions that the earlier "each ruleset has its own version" rule left open:

1. A world pins one `ruleset_id`/`ruleset_version` at creation and the registry fails closed
   on unknown versions. Bumping the only registered SRD version would therefore break every
   existing world, including live PocketRisu chats on 6011. Adding actions under the same
   version would instead make "0.1.0" mean different things on different deployments.
2. The SRD ties several automatic effects to hit points (unconsciousness at 0 HP, death
   saves, instant death). Implementing a few of them implicitly would silently expand the
   rules a world was created with and would make narration guess at unimplemented effects.

## Decision

**One rules class, several registered versions.** `SRD521Rules(version)` is constructed per
version and exposes only that version's closed action set. The registry registers every
supported version (`0.1.0`, `0.2.0`), so a world created earlier keeps exactly the actions and
semantics it was created with. `0.2.0` adds actions; it changes nothing that `0.1.0` resolves.
A test dispatches the same attack through both versions and requires identical state,
rolls, patches, resolution and facts, with only the version tag in the trace differing.

**New worlds pin the latest version; existing worlds are never migrated.** The PocketRisu
bridge creates new worlds on `LATEST_VERSION` and accepts an existing world's pinned version
as is. A `0.1.0` world rejects the new commands with a validation error rather than gaining
rules mid-campaign. Exported packs describe the latest version; the registry keeps the rest.

**State actions are explicit, closed and dice-free.** `damage` and `heal` take a target and an
amount declared by the GM and clamp HP to `0..max_hp`. `apply_condition` and
`remove_condition` take one named SRD condition; applying a present condition or removing an
absent one is rejected before any write, so every committed entry changes something.
`advance_time` adds explicit seconds (six-second rounds, minutes, hours, days) to
`simulated_time`. None of them roll dice or read the clock; replay needs no tool results.

**Nothing happens automatically at 0 HP.** Unconsciousness, death saves and instant death
remain unimplemented and are listed as such in the manifest. The narrator instructions say
so, and the GM proposes `apply_condition … unconscious` explicitly when the fiction calls for
it. Conditions are recorded only; they do not yet modify rolls. `exhaustion` is excluded until
its levels are tracked.

**Serialized state is order-independent.** `ActorState.conditions` stays a set for rule
logic but serializes sorted, so API responses, stored entries and benchmark artifacts do not
depend on the process hash seed. State hashes already canonicalized sets.

**Applied again for `0.3.0`.** Inventory arrived as `give`/`take` under the same rule: a
further registered version, exact counts per actor, item keys closed by shape
(`[a-z0-9_]{1,40}`) rather than by a catalogue, no item effects, and taking more than is
held rejected before any write. A catalogue with effects would be yet another version.

## Consequences

- Bridge command identities are unchanged: adding verbs (`damage`, `heal`, `inflict`,
  `cure`, `wait`) never changes the hash of an existing command, and natural-language
  proposals remain limited to roll actions.
- Each later SRD change must either be purely additive under a new version or introduce a
  further registered version. Removing a registered version requires an explicit, backed-up
  migration of every world that pins it; none exists yet.
- Manifests and packs grow with each version. The exported pack directory holds only the
  latest version; older manifests are available from the registry and the HTTP API.
- A rejected divergent edit no longer leaves an empty fork behind (see
  [persistence](../PERSISTENCE.md)); forks that committed anything are never discarded.

## Evidence

`tests/test_rulesets.py` covers each state action, fail-closed validation, replay from
recorded results, `0.1.0` rejection of new kinds and cross-version attack equivalence.
`tests/test_risu_api.py` covers the new commands, version pinning of new and existing worlds,
regeneration without dice and fork cleanup after a rejected edit. The live checkpoint is in
[the progress report](../progress/srd-state-actions.md).
