# ADR 0008: Rulebooks travel inside RisuAI modules as lorebook entries

Status: accepted (2026-10-06); the plugin side (sending the entry with a turn) is not built yet.

## Problem

The owner wants rulebooks delivered the way RisuAI creators already share things: as modules.
A module must reach the SAGA backend without a second file transfer, must not execute
anything, and must not let the model see the raw data. The host's module format was unknown
to this repository until read from the pinned PocketRisu 1.13.0 source
([RISUAI_MODULES.md](../RISUAI_MODULES.md)).

## Decision

**Export the host's plain JSON import shape.** `python -m saga_trpg.rulebooks module` writes
`{ "type": "risuModule", name, description, id, namespace: "saga", lorebook: [...] }`. The host
imports it from Settings → Modules → Import with no binary container, no RPack and no asset
pipeline. Ids are `uuid5` of `<id>@<version>`, so exports are byte-stable and reviewable.

**Two lorebook entries, no scripts.** A narration entry (`alwaysActive`, `constant`) carries the
prompt-facing instructions and scope lists. A rulebook entry carries the rulebook JSON verbatim
with `useRegex: true` and the never-matching key `(?!)`, so it is never inserted into the
prompt but is returned to plugins by `getCurrentLorebookEntries()`. Its `comment` is
`saga:rulebook:<id>@<version>:<sha256>`. The module has no `cjs`, `trigger` or `regex`.

**Import validates, stores and pins.** `POST /rulebooks { content }` runs the same loader as
bundled rulebooks, writes `<data dir>/rulebooks/<id>@<version>.json`, and registers the
definition. The same `<id>@<version>` may be re-registered only with byte-identical content; a
different file must bump the version. A bundled `<id>@<version>` can never be shadowed. On every
start the repository registers the stored files again, so worlds pinned to an imported rulebook
replay after a restart.

## Consequences

- A rulebook that reaches the backend through a module is exactly the file the creator
  checked with `saga_trpg.rulebooks check`; the sha256 in the comment lets the plugin skip
  re-sending and lets the backend detect tampering.
- Storage stays at schema v2: imported rulebooks are files beside the database, covered by
  the same backup instructions. They are not world data and never need migration.
- Still open: the plugin forwarding the entry with each turn, and a command vocabulary that
  is not SRD-shaped (today's `attack opponent` commands are hard-coded verbs; a Fate module
  needs `overcome`/`create_advantage`). Both are the next slice.
- If the host changes its module format, `RISUAI_MODULES.md` and `build_module` change
  together; the rulebook file itself does not.

## Evidence

`tests/test_risu_module.py`: export shape and entry flags, round trip through the entry
finder with hash verification, the `module` command, store persistence and conflict rules,
bundled-shadowing refusal, and the API importing a custom rulebook, creating a world on it,
dispatching, and replaying after a process restart.
