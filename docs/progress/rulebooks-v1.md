# Rulebooks v1: data-driven rules and the generic engine

Date: 2026-10-06 (Asia/Seoul).
Status: implemented and tested offline; the bundled `srd-5.2.1@0.4.0` rulebook is the
latest SRD and new PocketRisu worlds pin it. Not yet deployed to 6131 or 6011 at the time of
writing. See [ADR 0007](../adr/0007-rulebooks-as-data.md) and the
[creator guide](../RULEBOOKS.md).

## Changed

- `saga_trpg.rulebooks.expr`: closed expression language (integers, comparisons, boolean
  logic, conditionals, `max`/`min`/`abs`, dotted and indexed lookups), rejected at compile
  time for anything else; bounded size and magnitude; unknown names reported by name.
- `saga_trpg.rulebooks.loader`: rulebook schema v1 (stats with families and modifier
  formulas, check frames, outcome tables, check and builtin actions, conditions with roll
  effects, items, example world, narration) and cross-checks that report problems by entry
  path. `python -m saga_trpg.rulebooks check <file>` prints them.
- `saga_trpg.rulebooks.engine.DataRules`: the generic interpreter. Builds closed payload
  models from the file, validates stats on access, evaluates requirements, modifier, target
  and damage formulas before the first roll, merges advantage sources (inputs and condition
  effects) with one-cancels-one, grades through the outcome table, applies builtins.
- `src/saga_trpg/rulebooks/bundled/srd-5.2.1.json`: the SRD example as data, version `0.4.0`,
  adding condition effects on rolls and `cannot_act` for incapacitating conditions. Exported
  to `packs/rulesets/srd-5.2.1/rulebook.json` beside the manifest.
- Registry: `srd-5.2.1@0.4.0` is served from the rulebook; `0.1.0`–`0.3.0` stay as Python
  oracles. `SRD_LATEST` lives in the catalog; the Risu bridge pins it for new worlds.

## Follow-up: Fate Condensed as data (same day)

- `fate-condensed@0.2.0` is a rulebook; `0.1.0` stays as the Python oracle. Schema v1 grew
  by: roll `NdF±M` with `d3`, `pending` on outcome rules, `records` (typed collections in
  flags, validated before any roll), `on_outcome` record effects, `requires.unique`, text and
  integer input defaults, `actor.id`, `example_world.flags`.
- Nine equivalence scenarios (overcome and create_advantage across all four grades, custom
  boost name) match the Python module in state, patches and resolution; rolls match except
  the roll key (`overcome:0` instead of `overcome`). Duplicate aspect names and malformed
  existing records are rejected before dice.

## Follow-up: RisuAI module export and backend import (same day)

- Read the host's module format from the PocketRisu 1.13.0 source maps and recorded it in
  [RISUAI_MODULES.md](../RISUAI_MODULES.md): plain JSON `{type: "risuModule"}` is an
  accepted import shape; plugins read enabled modules' lorebooks without permission; a
  regex key `(?!)` keeps an entry out of the prompt.
- `python -m saga_trpg.rulebooks module` writes that shape with a narration entry and a
  rulebook entry (`saga:rulebook:<id>@<version>:<sha256>`); packs export `risu-module.json`.
- `POST /rulebooks` validates, stores `<data dir>/rulebooks/<id>@<version>.json`, registers
  the definition and refuses a different file under an existing or bundled version. Stored
  files are registered on every start; a world pinned to an imported rulebook replays after
  a restart (tested). See [ADR 0008](../adr/0008-rulebooks-in-risuai-modules.md).

## Follow-up: rulebook-derived commands and plugin forwarding (same day)

- `saga_trpg.rulebooks.commands`: `<action> <positional…> [name=value…] [advantage|disadvantage]`
  with SRD aliases; pure and canonical, so SRD command identities are unchanged (tested
  against the old shapes). `command_help` lists a rulebook's commands for error messages.
- Bridge: the turn may carry `rulebook: {content}`; a new world is pinned to it (validated and
  registered first, 422 before any world write on failure), an existing world ignores it.
  Commands are parsed with the world's rulebook; natural language stays SRD-only.
- Plugin 0.6.0 finds the `saga:rulebook:` entry among enabled lorebooks, verifies its sha256
  and forwards it. Starter guide and integration contract updated.

## Follow-up: `.risum` and `.charx` writers (same day)

- `rulebooks/risum.py`: the host's legacy binary module layout and `RPackMap` (validated
  512-byte inverse pair); `extract_rpack_map` reads the table from the host's shipped bundle
  on the user's machine. The table is not bundled and produced files are not committed,
  because a `.risum` of known JSON reveals the table.
- `rulebooks/charx.py`: CCv3 `card.json` lifted from the v2 starter card, a generated PNG
  icon at `assets/icon/image/main.png`, optional `module.risum`. A card with the rulebook
  module inside carries the rulebook as its own lorebook.
- CLI: `rpack-map`, `risum`, `charx`. Verified with the 6131 host's real table: `.risum`
  files decode back to the module with the matching rulebook entry; host import pending.

## Follow-up: rulebook-driven footer (same day)

- Schema: optional `display` section (`actor`, `records`, `world`) validated against the
  rulebook's stats and records; defaults reproduce the previous footer.
- `rulebooks/display.py` renders plain strings; the bridge adds `saga.display` to every
  response; plugin 0.7.0 prints them and falls back to the old rendering for older backends.
  SRD shows HP/conditions/inventory and time; Fate shows skills and aspects, no HP.

## Verification

- `pytest` 388 passed, `ruff` clean, `node --test` 23 passed, pack export check clean.
- 18 equivalence scenarios: attack (hit, miss, natural 1 and 20, advantage, disadvantage,
  both), checks with and without proficiency, saves, damage, heal, condition, time, give.
  The data engine's state, rolls, patches and resolution equal `SRD521Rules("0.3.0")`.
- Condition effects: prone attacker rolls twice and keeps the worse; paralyzed target grants
  advantage; poisoned attacker against paralyzed target cancels to one roll; poisoned actor
  has disadvantage on checks but heals normally; unconscious actor cannot attack or save but
  can have the condition removed.
- A nine-line "tiny" rulebook loads, runs with its own example world and rejects an
  advantage input its check frame does not allow.

## Findings

- Lookups needed a validating view rather than a plain dict so that a missing or malformed
  stat fails before dice with the stat's name, matching the Python modules' behaviour.
- Optional text inputs read as `""` in formulas so authors can write
  `input.proficiency != ""` without a null concept in the language.
- Family stats (`proficient:*`) were the only SRD feature that did not map to a flat key; the
  `actor.family[key]` view keeps the stored key shape unchanged.

- The bundled file first lived under `rulebooks/data/`, which `.dockerignore`'s `**/data`
  rule (meant for saved games) silently dropped from the image; the 6131 runtime crashed on
  start. The directory is `rulebooks/bundled/` for that reason.

## Unresolved

- Live verification on the 6131 host of a Fate module (JSON or `.risum`) and of a `.charx`
  import is pending.
- Spending free invokes, expiring boosts, opposed rolls and resource pools remain outside the
  schema; they would need either new builtins or the scripted layer.
- Item effects, durations, multi-step procedures and opposed rolls are out of schema v1.
- The 6131 and 6011 runtimes run older images; existing chats keep their pinned versions.

## Next recommended task

Import the Fate module on the 6131 host and play a new chat through it live. Then `.risum`
and `.charx` export with a supplied RPack table, and a "rulebook author" skill for AI-assisted
creators.
