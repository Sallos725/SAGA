# System-sized ruleset examples

Use one common bot description and select **one complete system identity** per campaign.
Each system currently implements a declared example subset; these are not full rulebook engines.

| Module | Source license | Implemented example |
| --- | --- | --- |
| `srd-5.2.1` version `0.1.0` | CC BY 4.0 | Ability checks, saving throws, configured weapon attacks, advantage/disadvantage, critical damage, HP |
| `fate-condensed` version `0.1.0` | CC BY 3.0 | Static overcome/new-aspect advantage rolls, outcome grades, free-invoke and boost records |

The original `generic-fantasy` remains available for existing demo worlds. It is not one of
these licensed-source examples.

## Files

- `common-bot-description.md`: system-independent GM instructions.
- `<module>/module.json`: identity/version, source/license/attribution, supported event schemas,
  narrator instructions, and explicit implementation boundaries.
- `<module>/prompt.md`: readable ruleset instructions and attribution.
- `<module>/initial-world.json`: example actor stats and initial state for the runtime.
- `<module>/events.json`: example validated action proposals.

The backend catalog is the generation source. Run `python scripts/export_rulesets.py` after
changing it; `python scripts/export_rulesets.py --check` detects missing or stale exports.
These are reviewable source packs, **not RisuAI-native `.risum`/card export files**. The actual
RisuAI Custom API and import format integration remain future work. Pasting the prompt alone
does not execute this backend or make model-written state authoritative.

## Run a campaign through the debug API

Use the project environment with `pip install -e '.[dev,api]'`, then start:

```bash
uvicorn saga_trpg.api.app:create_app --factory --port 4850
```

List the available modules and create a fantasy example:

```bash
curl -s http://127.0.0.1:4850/rulesets
curl -s -X POST http://127.0.0.1:4850/worlds/fantasy-example \
  -H 'content-type: application/json' \
  -d '{"ruleset_id":"srd-5.2.1","ruleset_version":"0.1.0"}'
curl -s -X POST http://127.0.0.1:4850/worlds/fantasy-example/events \
  -H 'content-type: application/json' \
  -d '{"event_id":"turn-1","kind":"attack","actor_id":"player","payload":{"target_id":"opponent"}}'
```

Create a Fate example on the same server:

```bash
curl -s -X POST http://127.0.0.1:4850/worlds/fate-example \
  -H 'content-type: application/json' \
  -d '{"ruleset_id":"fate-condensed","ruleset_version":"0.1.0"}'
curl -s -X POST http://127.0.0.1:4850/worlds/fate-example/events \
  -H 'content-type: application/json' \
  -d '{"event_id":"turn-1","kind":"create_advantage","actor_id":"player","payload":{"skill":"investigate","difficulty":2,"aspect_name":"Mapped escape route"}}'
curl -s http://127.0.0.1:4850/worlds/fate-example/ruleset
```

The final request returns the common description and the campaign's selected module manifest,
including its narrator instructions. A future RisuAI host should consume this persisted selection.
Do not select a different module from chat text on every turn.

World creation returns 201 and conflicts return 409. Repeating an event with the same identity
reuses the committed outcome; use a new identity for a new action. The response includes a
structured `resolution` with outcome, total, target, damage where applicable, and an explicit
`pending_adjudication` flag. Query `/worlds/{id}/state` for the current state after older retries.

## Input and state conventions

Both new modules reject unknown actions, extra payload properties, malformed types, missing
actors, and missing required actor stats **before rolling**. Declared difficulty/range limits
are implementation bounds, not a claim that the source systems have exactly those limits.
This is the existing trusted debug command surface, not a player-authorization layer: the GM
still adjudicates fictional permission, difficulty, advantage, and skill/proficiency relevance.

For SRD checks, use an ability name and difficulty. The optional `proficiency` on `ability_check`
names an applicable proficiency; the actual bonus is read from `proficient:<name>` and
`proficiency_bonus` in actor stats. Saves use `save_proficient:<ability>`. Attacks read
`weapon_ability`, `weapon_damage`, `weapon_proficient`, and the target's `armor_class` from state.
Payloads cannot override attack bonuses, damage dice, or AC. Weapon damage is a bounded count
of d4/d6/d8/d10/d12 dice without a modifier; the stored ability supplies the damage modifier.

For Fate, use a stored `skill:<name>` and static difficulty. New aspects are keyed by their
creating event id in `flags.fate_aspects` and record owner/name/free invokes. Boosts are separate
records in `flags.fate_boosts`; granting a boost does not silently create a persistent aspect.
The example rejects creating an already named aspect because existing-aspect mechanics are
out of scope. Generic actor HP fields are not Fate stress or health and are unused by this module.

## Limits and failure behavior

Unsupported rules return 422 without journal/state mutation or dice calls. Modules never
fall back to a different system. Persisted id/version selects the same handler after restart;
unknown stored versions return 503 rather than silently migrating. Existing world selection
cannot be replaced by a second create request. Forks inherit the world's selected system.

The manifests list missing mechanics. In particular, Fate invocations and boost expiry are
not automated. An overcome failure/tie or failed advantage leaves the source's fictional
choice pending; no cost, adverse aspect, or alternative success is silently applied. SRD HP
updates do not implement conditions, death saves, resistance, range, or turn legality.

Runtime identity, journal-before-response, snapshots, replay, branch isolation, and the
pre-commit external-tool failure limitation remain as documented in
[PERSISTENCE.md](../../docs/PERSISTENCE.md). Source terms and credits are recorded in
[RULESET_LICENSES.md](../../docs/RULESET_LICENSES.md) and must accompany redistributed packs.
