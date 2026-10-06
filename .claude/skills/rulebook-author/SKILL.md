---
name: rulebook-author
description: Write or extend a SAGA rulebook (a game system as one JSON file) with someone who may not know the system or the schema well. Use when asked to "make a rulebook", "add a system", "port <game> to SAGA", "add an action/condition/item to <rulebook>", or to package a rulebook as a RisuAI module.
---

# Rulebook author

A rulebook is one JSON file the generic engine interprets: stats, check frames, outcome
tables, actions, conditions, items, records, an example world, narration and a `display`
declaration. It contains no code. The full contract is `docs/RULEBOOKS.md`; read it before
writing anything, and `docs/adr/0007-rulebooks-as-data.md` if you need the reasoning.

The person you are helping may not know the game system or the schema. Your job is to turn
what they do know into a valid file, say plainly what you left out, and never invent rules
that the engine cannot express.

## Workflow

1. **Ask five things, no more.** Name of the system; how dice are rolled (d20 vs target,
   d100 roll-under, 4dF, 2d6 bands…); the numbers on a character sheet; the two or three
   actions they want to play first; the conditions that matter. If they do not know an
   answer, pick the simplest option and say so.
2. **Start from the nearest bundled file**, never from scratch:
   `packs/rulesets/srd-5.2.1/rulebook.json` for d20-style systems,
   `packs/rulesets/fate-condensed/rulebook.json` for graded results and records.
   Copy it to a new file, change `id`, `version` (`0.1.0`), `title`, `license`, and replace
   or remove `source` and `modifications` (they describe the SRD/Fate files, not the new
   system), then edit section by section.
3. **Check after every section**:
   ```bash
   python -m saga_trpg.rulebooks check path/to/book.json
   ```
   Errors name the entry (`actions.attack.check: unknown check 'd100'; defined: d20`). Fix
   the named entry; do not restructure the file to dodge an error.
4. **Write `not_implemented` honestly.** Anything the engine cannot express (multi-step
   procedures, resource pools, opposed rolls, item effects, durations) goes there, in plain
   words, so the narrator does not improvise it. This is a feature, not a failure.
5. **Prove it with the engine**, not by reading:
   ```python
   from saga_trpg.rulebooks.loader import load_rulebook
   from saga_trpg.rulebooks.engine import DataRules
   from saga_trpg.rulebooks.commands import parse_command, command_help
   spec = load_rulebook("path/to/book.json"); rules = DataRules(spec)
   print(command_help(spec))
   ```
   Then dispatch two or three commands through `RuntimeEngine` with scripted dice the way
   `tests/test_rulebooks.py` does, and show the resulting facts/state to the person.
6. **Package** when they are happy:
   ```bash
   python -m saga_trpg.rulebooks module path/to/book.json --out book.module.json
   ```
   Tell them: import under Settings → Modules, enable it, start a **new** chat. For
   `.risum`/`.charx` they need the host's RPack table (`docs/RISUAI_MODULES.md`); do not
   look for it in this repository, it is deliberately not here.

## Rules you must keep

- Formulas use the closed expression language only (`docs/RULEBOOKS.md` → Formulas). No
  floats, no functions beyond `max/min/abs`, no strings beyond comparisons. If a rule needs
  more, it is `not_implemented` or a new engine builtin, never a hack in the file.
- Actor ids are lower-case identifiers. Keys match `[a-z][a-z0-9_]{0,39}`. Item keys match
  `[a-z0-9_]{1,40}`. Enum members may use any case.
- A `records` collection needs `unique` fields before any `requires.unique` can guard it,
  and each `on_outcome` may write a collection once.
- Once a version has shipped, any change is a new `version`. Bundled files under
  `src/saga_trpg/rulebooks/bundled/` are immutable; a changed rule is a new
  `<id>@<version>.json` there, never an edit in place.
- Do not add Python under `src/saga_trpg/modules/` for a system. Those are oracles for the
  old hand-written versions, not the place for new rules.
- Licensing: `license.id` and `attribution` are required, `license.url` is optional. For SRD-derived
  content copy the attribution from the SRD file; for Fate-derived content from the Fate
  file; for original systems use the author's own license and name.

## Common asks and where they go

| Ask | Section |
| --- | --- |
| "roll under skill on d100" | `checks: { roll: "1d100" }`, outcome `when: "total <= target"` |
| "2d6, 6- miss, 7–9 partial, 10+ full" | one check `2d6`, three outcome rules on `total` |
| "advantage / disadvantage" | check frame `advantage: "roll_twice"` |
| "poisoned gives -2" | flat condition modifiers are not expressible yet → `not_implemented`; only if the creator explicitly prefers it, model it as a `conditions` advantage/disadvantage effect and say that it changes the rule |
| "track clocks / aspects / wounds" | `records` + `on_outcome`; spending/expiring needs builtins `record_adjust`/`record_remove` if present, else `not_implemented` |
| "show skills instead of HP in the footer" | `display.actor: ["skill:*"]`, `display.world: []` |

## Output

Hand back the file path, the `check` output, the command list, and the `not_implemented`
list in that order. If something they asked for is in `not_implemented`, say which and why
in one line each.
