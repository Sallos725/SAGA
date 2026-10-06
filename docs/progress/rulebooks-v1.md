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
- `src/saga_trpg/rulebooks/data/srd-5.2.1.json`: the SRD example as data, version `0.4.0`,
  adding condition effects on rolls and `cannot_act` for incapacitating conditions. Exported
  to `packs/rulesets/srd-5.2.1/rulebook.json` beside the manifest.
- Registry: `srd-5.2.1@0.4.0` is served from the rulebook; `0.1.0`–`0.3.0` stay as Python
  oracles. `SRD_LATEST` lives in the catalog; the Risu bridge pins it for new worlds.

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

## Unresolved

- No import path from a RisuAI module container yet; rulebooks are bundled with the package.
- Fate Condensed is still Python only. Expressing it as data needs Fate dice (`4dF`) in the
  check frame (allowed by the schema) and graded `success` rules; its pending-cost semantics
  need an engine notion of `pending_adjudication`, which the schema does not have.
- Item effects, durations, multi-step procedures and opposed rolls are out of schema v1.
- The 6131 and 6011 runtimes run older images; existing chats keep their pinned versions.

## Next recommended task

Deploy to 6131 and play through a conditions scenario live; then either express Fate as data
(adds `pending_adjudication` and graded outcomes to the schema) or build the RisuAI module
import path, whichever the owner wants first. A "rulebook author" skill for AI-assisted
creators can sit on top of the creator guide once the schema has survived a second system.
