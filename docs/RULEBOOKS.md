# Writing a rulebook

Working with Claude Code? The repository ships a `rulebook-author` skill
(`.claude/skills/rulebook-author/SKILL.md`) that walks through this page with you: five
questions, copy the nearest bundled file, check after every section, prove it with the engine,
package it as a module.

A rulebook is one JSON file that tells SAGA how a game system works: which numbers a
character has, how dice are rolled, how results are graded, which actions exist, what
conditions do and which items exist. SAGA's generic engine reads the file; the file contains
no code. You do not need to know Python, and you do not need to know every rule of the system
you are describing. Describe the part you want to play with; list the rest under
`not_implemented`.

Start from [`packs/rulesets/srd-5.2.1/rulebook.json`](../packs/rulesets/srd-5.2.1/rulebook.json)
and change values, or from the tiny example at the end of this page. Check your file with:

```bash
python -m saga_trpg.rulebooks check path/to/rulebook.json
```

Problems are reported by entry, for example
`actions.attack.check: unknown check 'd100'; defined: d20`. Fix the entry and run again.

## The file, section by section

```jsonc
{
  "schema_version": 1,
  "id": "my-system",            // lowercase letters, digits, dots, hyphens
  "version": "0.1.0",           // major.minor.patch; bump it when rules change
  "title": "My System",
  "license": { "id": "CC-BY-4.0", "url": "...", "attribution": "required credit text" },
  "source": { "url": "...", "revision": "what you read and when" },   // optional
  "modifications": "what you changed from the source",               // optional

  "stats": { ... },        // 1. numbers, flags and labels on a character
  "checks": { ... },       // 2. how dice are rolled
  "outcomes": { ... },     // 3. how a roll becomes success / failure / hit ...
  "actions": { ... },      // 4. what a player can do
  "conditions": { ... },   // 5. named states and what they do to rolls
  "items": { ... },        // 6. optional closed item list
  "records": { ... },      // 6b. optional typed record collections (aspects, boosts, clocks…)
  "example_world": { ... },// 7. a starting cast for demos and tests
  "display": { ... },      // 8. what the status footer shows
  "implemented": [ ... ],  // plain-language scope, shown to the narrator and in docs
  "not_implemented": [ ... ],
  "narration": "..."       // instructions for the narrating model
}
```

### 1. `stats` — what is on the character sheet

```json
"stats": {
  "strength":          { "type": "integer", "range": [1, 30], "modifier": "(value - 10) // 2" },
  "armor_class":       { "type": "integer", "range": [1, 100] },
  "weapon_ability":    { "type": "text", "enum": ["strength", "dexterity"] },
  "weapon_damage":     { "type": "text", "pattern": "^[1-9]d(4|6|8|10|12)$" },
  "weapon_proficient": { "type": "boolean", "default": false },
  "proficient:*":      { "type": "boolean", "default": false }
}
```

- `integer` stats need a `range`. An optional `modifier` formula turns the raw value into
  the number added to rolls; `value` is the only name available in it.
- `text` stats need an `enum` (closed list) or a `pattern` (regular expression).
- `boolean` stats may have a `default`. Any `default` must satisfy its own range, enum or
  pattern; the checker says so otherwise.
- A key ending in `:*` is a **family**: `proficient:*` covers `proficient:athletics`,
  `proficient:stealth`, …, each with the family's default. Formulas read a family as
  `actor.proficient[name]`.
- A stat without a default must be present on the actor when a formula reads it; a missing or
  out-of-range value rejects the action before any dice are rolled, with the stat named.

### 2. `checks` — how dice are rolled

```json
"checks": {
  "d20": { "roll": "1d20", "advantage": "roll_twice" }
}
```

`roll` is `NdF[±M]` with 1..100 dice, F in 3, 4, 6, 8, 10, 12, 20 or 100 and an optional fixed
modifier. Fate dice are written `4d3-8` (each d3 face 1/2/3 stands for −/blank/+; the journal
keeps the raw faces). `advantage: "roll_twice"`
enables advantage/disadvantage: the action accepts `advantage` and `disadvantage` booleans,
conditions can add either, one of each cancels out, and the better or worse of two rolls is
kept. `"none"` rolls once and rejects those inputs.

### 3. `outcomes` — grading a roll

```json
"outcomes": {
  "attack": [
    { "when": "die == 20", "outcome": "critical_hit" },
    { "when": "die == 1",  "outcome": "miss" },
    { "when": "total >= target", "outcome": "hit" },
    { "when": "total < target",  "outcome": "miss" }
  ]
}
```

Rules are tried top to bottom; the first true `when` wins; if none matches the action is
rejected. Names available: `die` (the kept die), `total` (die plus modifier), `target`.
Add `"pending": true` to a rule when that result leaves a choice to the GM (Fate's failure
or tie costs): the resolution is marked `pending_adjudication` and the narrator is told that
nothing was applied.
Outcomes must be one of `failure`, `tie`, `success`, `success_with_style`, `miss`, `hit`,
`critical_hit`. Fate-style graded results use several `success` rules with different `when`.

### 4. `actions` — what a player can do

Two shapes. A **check action** rolls dice:

```json
"attack": {
  "role": "attack",                       // attack | check | save (picks condition effects)
  "input": { "target_id": { "type": "actor" } },
  "requires": [ { "when": "actor.hp > 0", "error": "attack requires a conscious attacker" } ],
  "check": "d20",
  "modifier": "mod[actor.weapon_ability] + (actor.proficiency_bonus if actor.weapon_proficient else 0)",
  "target": "target.armor_class",
  "outcomes": "attack",
  "on_hit": { "dice": "actor.weapon_damage", "bonus": "mod[actor.weapon_ability]", "critical": "double_dice" }
}
```

- `input` fields: `integer` (needs `range`), `text` (needs `enum` or `pattern`), `actor` (an
  actor id), `boolean`. Add `"optional": true` for an optional field or a `default` value.
  Do not declare `advantage`/`disadvantage`; the check frame adds them.
- `requires` are checked in order before anything is rolled. Either a formula
  (`{ "when": "actor.hp > 0", "error": "…" }`) or a uniqueness rule over a record collection
  (`{ "unique": { "record": "fate_aspects", "field": "name", "value": "input.aspect_name" }, "error": "…" }`).
- `on_outcome` writes records when a given outcome happens:
  `"on_outcome": { "success": [ { "record": "fate_aspects", "fields": { "name": "input.aspect_name", "owner_id": "actor.id", "free_invokes": "1" } } ] }`.
  Every field of the record must be given as a formula; the record is stored under the event
  id in `flags.<record>` and appears in the patches.
- `modifier` and `target` are formulas. `on_hit` is only for `attack`-role actions: `dice` must
  evaluate to a dice expression such as `1d8` (at most 100 dice after a critical doubles the
  count), `bonus` to a number; on `critical_hit` the dice count doubles when `critical` is
  `double_dice`. Damage reduces the target's HP, never below zero.

A **builtin action** changes state without dice and needs no formulas:

```json
"damage":           { "builtin": "hp_change", "sign": -1 },
"heal":             { "builtin": "hp_change", "sign": 1 },
"apply_condition":  { "builtin": "condition_add" },
"remove_condition": { "builtin": "condition_remove" },
"advance_time":     { "builtin": "time_advance", "units": { "round": 6, "minute": 60 } },
"give":             { "builtin": "inventory_add" },
"take":             { "builtin": "inventory_remove" }
```

Their inputs are fixed: `target_id` plus `amount` (1..1000) for HP and items, `condition`
(one of your `conditions`) for conditions, `item` for items, `amount` plus `unit` for time.
Two more work on `records`: `{ "builtin": "record_adjust", "record": "fate_aspects",
"field": "free_invokes", "sign": -1 }` changes an integer field of the record named by the
collection's first `unique` field (input `<unique field>` plus optional `amount`, rejected when
the result leaves the field's range), and `{ "builtin": "record_remove", "record":
"fate_boosts" }` deletes the named record. A collection must declare `unique` for either.
HP is clamped to `0..max_hp`; adding a present condition or removing an absent one is
rejected; taking more than is held is rejected. Nothing happens automatically at 0 HP unless
your actions say so.

One more moves the acting actor between rooms: `"move": { "builtin": "map_move" }`. Its only
input is `exit`, the key of an exit of the actor's current room (`move north`). The key is
checked against the room when the event is applied, so a command never depends on where the
actor stands; a move from a world without a map, or through an exit the room does not have,
is rejected without a journal entry. An actor with a condition whose `cannot_act` is set cannot
move. Moving does not advance time.

### 5. `conditions` — named states and their effect on rolls

```json
"conditions": {
  "prone":      { "attacker": "disadvantage" },
  "blinded":    { "attacker": "disadvantage", "target": "advantage" },
  "poisoned":   { "attacker": "disadvantage", "check": "disadvantage" },
  "paralyzed":  { "target": "advantage", "cannot_act": true },
  "charmed":    {}
}
```

- `attacker` applies when the actor attacks, `target` when the actor is attacked, `check` to
  check-role actions, `save` to save-role actions. Values are `advantage` or `disadvantage`.
- `cannot_act: true` rejects every check action by that actor until the condition is removed.
  Builtin actions (healing, removing the condition) still work.
- The engine writes every applied effect into the trace, so a replay shows why two dice were
  rolled.

### 6. `items`

Leave `items` empty to accept any key of the shape `[a-z0-9_]{1,40}`. List items to close the
set: `"torch": { "name": "Torch" }`. Items have no effect in schema v1.

### 6b. `records` — typed collections in world flags

```json
"records": {
  "fate_aspects": {
    "fields": { "name": { "type": "text", "pattern": "^.{1,200}$" },
                "owner_id": { "type": "actor" },
                "free_invokes": { "type": "integer", "range": [0, 10] } },
    "unique": ["name"]
  }
}
```

Records live in `flags.<name>` keyed by the event that created them. Existing records are
validated against the field types before any roll, so a hand-edited world cannot corrupt a
session silently. An `on_outcome` write that would duplicate a `unique` field is rejected
before any roll, so unique really means unique. `unique` lists the fields a `requires.unique` rule may guard; guarding any
other field is rejected. Within one outcome, each record collection may be written once.

### 7. `example_world`

```json
"example_world": {
  "actors": {
    "player":   { "hp": 20, "max_hp": 20, "stats": { "strength": 16, "...": "..." } },
    "opponent": { "hp": 30, "max_hp": 30, "stats": { "armor_class": 13 } }
  }
}
```

Used by demos, tests and new PocketRisu worlds. Keep it small and valid against your stats.
Actor ids are lower-case identifiers so commands can address them (`attack opponent`);
enum members may use any case and commands match them case-insensitively.
Add `"flags": { "fate_aspects": {} }` to start record collections empty.

`map` (optional) gives the world a room graph ([ADR 0009](adr/0009-locations-and-map-movement.md)).
Hand-authored:

```json
"map": {
  "start_room": "hall",
  "rooms": {
    "hall":  { "name": "입구 홀", "description": "…",
               "exits": { "north": { "to_room": "guard", "label": "북쪽 문", "kind": "door" } } },
    "guard": { "name": "경비실",
               "exits": { "south": { "to_room": "hall", "label": "남쪽 문", "kind": "door" } } }
  },
  "placements": { "opponent": "guard" }
}
```

Room ids and exit keys are lower-case identifiers; exits are one-way, so a two-way passage is
an exit in each room. `kind` is `open` (the next room's name is visible) or `door` (it shows as
`???` until visited). Every room must be reachable from `start_room`. A hand-authored map has at
most 200 rooms. Exit labels are shown to the player and the narrator even when the door is
closed, so do not name what lies behind them ("북쪽 문", not "보물고 문"). Actors without a
placement start in `start_room`.

Generated: `"map": { "generate": { "seed": "my-dungeon", "rooms": [6, 10] } }` builds 2..30
rooms from the seed and the world id, so each RisuAI chat gets its own dungeon and the same
chat always the same one. The generated map is stored when the world is created; later
changes to the generator never alter it.

### 8. `display` — what the status footer shows

```json
"display": {
  "actor": ["hp", "conditions", "inventory"],   // per actor; or stat names, or a family such as "skill:*"
  "records": ["fate_aspects"],                   // record collections to list (empty ones are skipped)
  "world": ["time", "location"]                  // world-level items; [] hides time; "location" shows the player's room and exit keys
}
```

The backend renders these into plain strings with every response and the host footer prints
them, so a Fate card shows skills and aspects instead of hit points without any host change.
Defaults when omitted: `actor: ["hp", "conditions", "inventory"]`, `world: ["time"]`.

## Formulas

Formulas use a small, closed language: integers, text in quotes, `+ - * // %`, comparisons,
`and or not` (short-circuit, so `x != 0 and 10 // x > 1` is safe), `x if condition else y`,
`max(a, b, …)`, `min(a, b, …)`, `abs(a)`, and lookups. There are no floats, no other functions
and no way to change anything.

| Name | Meaning | Available in |
| --- | --- | --- |
| `actor.<stat>` | the acting actor's validated stat, plus `actor.id`, `actor.hp`, `actor.max_hp` | actions |
| `actor.<family>[key]` | a family stat such as `actor.proficient[input.proficiency]` | actions |
| `mod[<stat>]` | the stat's `modifier` formula applied to the actor's value | actions |
| `input.<field>` | the validated action input; an optional text field that was omitted reads as `""` | actions |
| `target.…`, `target_mod[…]` | the same views of the `target_id` actor | actions with a `target_id` actor input |
| `value` | the raw stat value | `stats.*.modifier` |
| `die`, `total`, `target` | the kept die, die plus modifier, the target number | `outcomes` |

A formula that refers to an unknown name is rejected when the file is checked. A formula that
produces something other than a whole number where one is needed rejects the action at play
time, before dice.

## Versions and existing campaigns

A world pins the rulebook `id` and `version` it was created with and keeps it for its whole
life. Changing rules therefore means publishing a new `version`; old worlds keep replaying
under the old one. Any change to the file, even prose or `display`, needs a new version once
the file has been shipped: the backend refuses a different file under a known version, and a
module exported earlier must stay importable byte for byte.

## Shipping it as a RisuAI module

```bash
python -m saga_trpg.rulebooks module my-system.json --out my-system.module.json
```

Import the file in PocketRisu under Settings → Modules → Import and enable the module. It
holds two lorebook entries: your `narration` text (always inserted into the prompt) and the
rulebook itself (never inserted; the SAGA plugin reads it and sends it to the backend, which
validates and pins it). The backend keeps one file per `<id>@<version>`; to change rules,
bump `version` and export again. See [RISUAI_MODULES.md](RISUAI_MODULES.md) for the host
format and [ADR 0008](adr/0008-rulebooks-in-risuai-modules.md) for the contract. A backend
can also receive a rulebook directly: `POST /rulebooks` with `{"content": "<the JSON text>"}`.

For the host's binary shapes, `python -m saga_trpg.rulebooks risum` writes a `.risum` and
`python -m saga_trpg.rulebooks charx <card.json> --rulebook <rulebook.json>` writes a `.charx`
card with the rulebook inside. Both need the host's RPack table once
(`python -m saga_trpg.rulebooks rpack-map <host dist/assets>`); see
[RISUAI_MODULES.md](RISUAI_MODULES.md).

## Not in schema v1

Item effects and equipment, spells, initiative and action economy, multi-step procedures such
as death saves, condition durations, opposed rolls, invoke effects on rolls and resource
pools. Describe them under
`not_implemented` so the narrator does not improvise them. A later schema version or a
scripted layer (see ADR 0007) may add them.

## A tiny complete rulebook

```json
{
  "schema_version": 1,
  "id": "tiny",
  "version": "0.1.0",
  "title": "Tiny",
  "license": { "id": "CC0-1.0", "attribution": "none" },
  "stats": { "might": { "type": "integer", "range": [1, 10] } },
  "checks": { "d6": { "roll": "1d6", "advantage": "none" } },
  "outcomes": { "basic": [
    { "when": "total >= target", "outcome": "success" },
    { "when": "total < target",  "outcome": "failure" } ] },
  "actions": {
    "try":  { "role": "check", "input": { "difficulty": { "type": "integer", "range": [1, 20] } },
              "check": "d6", "modifier": "actor.might", "target": "input.difficulty", "outcomes": "basic" },
    "hurt": { "builtin": "hp_change", "sign": -1 }
  },
  "conditions": { "dazed": { "cannot_act": true } },
  "example_world": { "actors": { "player": { "hp": 5, "max_hp": 5, "stats": { "might": 3 } } } }
}
```
