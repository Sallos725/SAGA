# ADR 0007: Rulebooks are data interpreted by one generic engine

Status: accepted (2026-10-06)

## Problem

The owner's intent is a generic SAGA core with rulebooks delivered separately, in the shape
RisuAI creators already know (modules), by people who are not programmers. Until now every
system rule lived in Python under `modules/`: SRD checks, attacks, state actions and
inventory. Continuing that way makes the engine a growing rulebook collection, keeps creators
out, and would eventually tempt "condition X gives advantage" branches per system.

Three layers could travel in a module: **A** narration text, **B** tables and schemas the
engine interprets, **C** scripts. A is already supported. C needs a deterministic sandbox,
script hashes pinned per world, and reversal of the catalog's "no downloaded code" rule.

## Decision

**Rulebooks are B-layer data.** A rulebook is one JSON document validated against a closed
schema (`saga_trpg.rulebooks.loader`): stats, check frames, outcome tables, actions
(check or builtin), conditions with roll effects, items, an example world and narration.
`DataRules` (`saga_trpg.rulebooks.engine`) is the only interpreter and contains no
system-specific rule. Builtin actions (`hp_change`, `condition_add/remove`, `time_advance`,
`inventory_add/remove`) are the core's primitive operations; check actions compose a check
frame, formulas and an outcome table.

**Formulas use a closed expression language**, not a scripting language. Python expression
syntax is parsed and reduced at compile time to integer arithmetic, comparisons, boolean
logic, conditionals, `max`/`min`/`abs` and lookups into validated views. No floats, no
attribute access on objects, no calls, no assignment, bounded size and magnitude. Unknown
names are rejected when the file is checked; evaluation is pure. This is the "grey zone"
between tables and scripts, and it covers modifiers, targets, damage bonuses and outcome
grading without opening a sandbox.

**Condition effects come from the table.** A condition declares `attacker`, `target`,
`check`, `save` (advantage or disadvantage) and `cannot_act`. The engine merges effects with
the action's own advantage inputs under the one-cancels-one rule and records every source in
the trace. Nothing about any specific condition is in code.

**The Python SRD versions stay as oracles.** `srd-5.2.1@0.1.0..0.3.0` remain registered for
the worlds that pin them and serve as the reference implementation: an equivalence test runs
the same scripted dice and events through `SRD521Rules("0.3.0")` and the bundled
`srd-5.2.1@0.4.0` rulebook and requires identical state, rolls, patches and resolution for
every roll and state scenario without conditions. New Risu worlds pin the rulebook version.

**C stays closed for now.** Multi-step procedures (death saves, sanity), resource economies
and system-specific loops are listed as not implemented. If a real rulebook cannot be
expressed as data plus formulas, a further ADR decides whether to add a sandboxed scripting
layer or more builtins. Until then the catalog still accepts no downloaded code.

**Second system, same day.** Fate Condensed became `fate-condensed@0.2.0` as data. It needed
five declarative additions, all inside the B layer: `d3` dice with a fixed modifier (`4d3-8`),
`pending` on outcome rules, typed `records` collections in world flags, `on_outcome` effects
that write a record, and a `unique` form of `requires`. Nine equivalence scenarios match the
Python module in state, patches and resolution; roll records match except for the generic
`<kind>:<index>` roll key. No scripting was required.

## Consequences

- Creators edit one file, run `python -m saga_trpg.rulebooks check`, and get errors by entry
  path in plain language. Writing a rulebook needs no Python and no full knowledge of the
  system; `not_implemented` is a first-class answer.
- Rulebook validation is stricter than the Python modules were: stats are typed, inputs are
  closed, formulas must name only known things. A world's rules are exactly its file.
- The RisuAI module container (lorebook text plus the rulebook as a data asset) is the
  intended delivery vehicle; the import path that validates and pins a rulebook from such a
  container is not built yet. Until then rulebooks are bundled with the package.
- Expression language extensions (new functions, string operations) and new builtins are
  engine changes with their own tests; they must stay pure and deterministic.
- Equivalence against the Python oracle only covers what the oracle implements. New data-only
  behaviour (condition effects) is tested directly.

## Evidence

`tests/test_rulebook_expr.py` (language limits), `tests/test_rulebooks.py` (loader errors by
path, the `check` command, 18 equivalence scenarios, same rejections before dice, stat
validation, replay, condition effects, a creator-sized tiny rulebook, registry wiring) and
`tests/test_risu_api.py` (new bridge worlds pin `0.4.0`; condition effects reach the bridge).
