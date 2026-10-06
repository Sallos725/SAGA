# RisuAI modules: format and how a rulebook travels in one

Read from the PocketRisu 1.13.0 host source (image digest
`409a5243…`, sources recovered from the shipped source maps) on 2026-10-06. File paths below
are the host's `src/` tree. This is a record of what the host does, not a specification SAGA
controls; re-check it when the pinned host changes.

## The module object

`src/ts/process/modules.ts:20`:

```ts
interface RisuModule {
  name: string; description: string; id: string
  lorebook?: loreBook[]; regex?: customscript[]; trigger?: triggerscript[]; cjs?: string
  assets?: [name, path, ext][]; assetManifest?: …; namespace?: string
  lowLevelAccess?: boolean; hideIcon?: boolean; backgroundEmbedding?: string
  customModuleToggle?: string; mcp?: MCPModule; icon?: string; folderId?: string
}
```

A lorebook entry (`src/ts/storage/database.svelte.ts:1677`):

```ts
interface loreBook {
  key: string; secondkey: string; insertorder: number; comment: string; content: string
  mode: 'multiple' | 'constant' | 'normal' | 'child' | 'folder'
  alwaysActive: boolean; selective: boolean; useRegex?: boolean; id?: string; folder?: string
  extentions?: { risu_case_sensitive: boolean }; activationPercent?: number; bookVersion?: number
}
```

## Three import shapes (`importModule`, `modules.ts:270`)

| File | What the host does |
| --- | --- |
| `*.risum` | Binary: byte `111`, byte `0` (version), u32 LE length, RPack-encoded JSON `{ type: "risuModule", module }`, then per asset byte `1` + u32 length + RPack(compressImage(bytes)), then byte `0`. RPack is a fixed 256-byte substitution table (`src/ts/rpack/rpack_js.js`, map in `rpack_map.bin`). |
| `*.charx` | A character card v3 zip; the character is converted back into a module (`convertCharacterToModule`, `src/ts/interchangeability.ts:62`). The newer "Export" button writes this shape as `<name>.module`. |
| `*.json` | Parsed as JSON. If `type == "risuModule"` and `name` and `id` are present, the object is pushed into `db.modules` as is (a fresh `id` is assigned; `lowLevelAccess` prompts for confirmation). Other JSON shapes (`type: "risu"` lorebook export, `{ entries }` external lorebook, `type: "regex"`) become lorebook- or regex-only modules. |

SAGA therefore exports the **plain JSON shape**. No binary, no asset pipeline, and the file is
reviewable by eye.

## What a plugin can read

- `api.getCurrentLorebookEntries()` (`src/ts/plugins/apiV3/v3.svelte.ts:1156`) returns the
  character's global lore, the current chat's local lore and **every enabled module's
  lorebook** (`getModuleLorebooks`, `modules.ts:471`), with no extra permission.
- "Enabled" (`getModules`, `modules.ts:436`) means the global toggle, the chat's modules, the
  character's modules, a persona's embedded module and `moduleIntergration`.
- `api.getDatabase()` exposes `modules` too but needs the `db` permission prompt.
- Module assets go through `compressImage` on export/import; they are for images, not data.

## Keeping an entry out of the prompt

Lorebook activation (`src/ts/process/lorebook.svelte.ts:548`): an entry is inserted when it is
`alwaysActive`, force-activated by a decorator, or one of its `key`s matches the scanned chat.
With `useRegex: true` the key is a regular expression, so `key: "(?!)"` (an empty negative
lookahead) never matches, and with `alwaysActive: false`, `mode: "normal"`,
`selective: false` and no decorators the entry is never inserted. The content still travels
with the module and is still returned by `getCurrentLorebookEntries()`.

## How a rulebook travels

A SAGA module JSON (`python -m saga_trpg.rulebooks module <rulebook.json>`) contains:

1. **Narration entry** — `comment: "saga:narration:<id>@<version>"`, `alwaysActive: true`,
   `mode: "constant"`, content = the rulebook's narration text plus its implemented /
   not-implemented lists. This is the prompt-facing part.
2. **Rulebook entry** — `comment: "saga:rulebook:<id>@<version>:<sha256>"`, never-matching
   regex key, content = the rulebook JSON verbatim. This is the data part.

The plugin finds the rulebook entry among the enabled lorebooks and sends its content to the
backend with the turn; the backend validates it with the same loader as bundled rulebooks,
stores it under the data directory, registers `<id>@<version>` and pins new worlds to it. The
sha256 in the comment lets the plugin skip resending a rulebook the backend already holds and
lets the backend refuse a changed file that reuses an existing `<id>@<version>`.

Nothing in the module executes: no `cjs`, no triggers, no regex scripts. Rules stay data.
