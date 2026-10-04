# Local RisuAI dice references

Reviewed on 2026-10-04. The owner supplied two files under `~/saga-ref/dice/` as
implementation references. This review inspected the JavaScript source and decoded the
Risu module as data; it did not execute the plugin, import it into RisuAI, or verify a
backend-to-host connection. No reference code or assets were imported into SAGA.

## Source inventory

The reference directory is outside this repository and must be supplied separately on
another machine. Filenames and hashes identify the reviewed versions:

| File under `~/saga-ref/dice/` | Bytes | Role |
| --- | ---: | --- |
| `D&D Dice v2.0.0.risum` | 7,109 | Lorebook prompt, MCP binding, display rules, and user toggles |
| `dnd-dice-mcp-v3_2.1.0.js` | 24,770 | RisuAI plugin API 3.0; plugin/tool-provider version 2.1.0 |

SHA-256:

```text
758e1a7cdf83b3883d8209b38cd197dde837470a801d9ec44ae557b7585bddf2  D&D Dice v2.0.0.risum
ed6167a50f74db005258368776c9a3fe6e37af1eb5e3d43fa9a6c690e626a8be  dnd-dice-mcp-v3_2.1.0.js
```

Recheck before relying on source locations or behavior:

```bash
sha256sum "$HOME/saga-ref/dice/D&D Dice v2.0.0.risum" \
  "$HOME/saga-ref/dice/dnd-dice-mcp-v3_2.1.0.js"
```

On macOS, use `shasum -a 256` in place of `sha256sum`.
Neither supplied file contains an explicit reuse license; record provenance and confirm
reuse terms before copying source or assets. Describing the integration does not require
making them repository dependencies.

## Risu module contents

The `.risum` header contains magic byte `111`, format version `0`, and a little-endian
32-bit payload length. The 7,102-byte payload was decoded using the byte substitution
table from locally available RisuAI `rpack` source, then parsed as JSON with
`type: "risuModule"`. The remaining byte is the `0` end marker; there are no appended
assets in this file. Decoding did not execute embedded content.

The module contains:

- One always-active lorebook entry, `D&D Dice Protocol`, with MCP context directives.
  It asks the model to choose DCs, call tools for uncertain actions, honor their results,
  and render a bracketed dice summary. This is prompt guidance, not enforced validation.
- `mcp.url = "plugin:dnd-dice"`, matching the plugin's registration identifier.
- Three toggles: dice summaries in model context, basic/detailed roll triggers, and
  on-screen result visibility.
- Seven regex rules: one output-format correction, five display transformations, and
  one context-filtering rule. `backgroundEmbedding` supplies result-badge CSS.
- An empty trigger list and empty `cjs` field. The rolling implementation is in the
  companion plugin, not the module.

Useful JSON locations are `module.lorebook`, `module.mcp`, `module.customModuleToggle`,
`module.regex`, and `module.backgroundEmbedding`.

## Plugin contract observed in source

`Risuai.registerMCP` registers `plugin:dnd-dice` with tool listing and invocation
callbacks (JavaScript lines 477–502). This is a RisuAI-hosted provider; the supplied
file does not expose a standalone HTTP or stdio MCP server for SAGA to connect to.

| Tool | Inputs | Result fields of interest |
| --- | --- | --- |
| `roll_dice` | `notation`, matching `[count]d[sides][+/-modifier]` | `notation`, `rolls`, `modifier`, `total`, `max`, `description` |
| `check` | `type`: `skill`, `save`, `attack`, or `contest`; DC/AC, modifiers, optional damage/name; optional `checks` array | Type-specific roll and outcome fields; attacks include damage rolls; contests include both participants; batches contain nested results |

Tool schemas are at lines 235–280; handlers are at lines 284–473. The callback returns
MCP text content containing a JSON-serialized result. Errors are also JSON objects with
an `error` field inside that text content; they are not automatically a successful roll.

The plugin uses `Math.random()` and offers configurable dice/modifier limits, a
`forceResult` setting, and optional DOM-based dice animation. The animation functions
provide a useful reference for presenting an already chosen number.

## Applying the reference to SAGA

Keep game rules in the selected ruleset. A possible adapter should use `roll_dice` for
raw dice and let SAGA determine success, damage, and state changes. The combined `check`
handler is a reference for interaction design, not the authority for SAGA mechanics.
Any model-proposed DC or action must pass through validated `GameEvent` handling.

The existing [MCP adapter](../src/saga_trpg/adapters/dice/mcp.py) is a placeholder.
An implementation must use `ToolPort` through the repository's `external_dice`
`ToolBinding`, following the [durable result tape contract](PERSISTENCE.md#durable-external-tool-result-tape).
Do not inject a remote client as a bare `DicePort` or execute it inside deterministic
runtime code.

For a prospective `roll_dice` binding:

| SAGA value | Proposed mapping |
| --- | --- |
| `ToolCall.arguments.expression` | Send as tool argument `notation` |
| `RollRecord.roll_key` | Preserve the requested `roll_key` |
| `RollRecord.expression` | Preserve the requested expression after checking that returned notation represents the same dice and modifier |
| `RollRecord.raw` | Validated plugin `rolls` |
| `RollRecord.modifier`, `total` | Validated plugin values; require `total == sum(raw) + modifier` |

Validate integer types, dice count, each die's bounds, and modifier as well as the
arithmetic. The plugin silently clamps excessive counts, sides, and modifiers. Reject
a changed request instead of recording a clamped roll under the original expression.
`max` and `description` are presentation data, not authoritative state.

The tape must claim an execution before invoking the tool and persist its result before
event commit. Committed retries, replay, and narration regeneration reuse recorded
results. The supplied plugin accepts no event/idempotency key and exposes no result
lookup, so it cannot resolve an ambiguous timeout by itself. Follow the existing
[failure policy](PERSISTENCE.md#failure-and-compatibility-policy); never blindly re-roll
a pending execution. A transport and reconciliation design remain prerequisites for
live use.

Use the module's badges and the plugin's animation as presentation references for
committed results. Generate authoritative summaries from those results, rather than
extracting game state from model-written dice tags. Benchmark runs need fixed-seed or
pre-recorded outcomes, rather than this plugin's uncontrolled randomness.

## Compatibility details to preserve in follow-up work

- `d20Check` applies natural-20 success and natural-1 failure to skill checks and saves
  as well as the separate attack handler. Treat these as the plugin's semantics;
  SAGA's selected ruleset must define its own behavior.
- `forceResult` can declare success/failure independently of whether the numeric total
  meets the target. The handler reads this setting on each check and does not reset it,
  despite its setting description referring to the next check.
- The plugin emits contest results `WIN` and `LOSE`, but the module's success/failure
  display regexes expect `ACTOR WINS` and `OPPONENT WINS`. In source, those outcomes
  therefore fall through to the generic dice badge. The differing module/plugin
  versions should not be assumed to have fully matching presentation behavior.
- Neither file supplies stable SAGA action identity, world/branch binding, journal
  persistence, or regeneration deduplication. Use the existing
  [RisuAI identity reference](RISUAI_INTEGRATION.md#host-identity-reference-oneuldo-memory-pilot)
  when designing the host bridge.

## Verification scope and next step

Confirmed by static inspection: source hashes, module structure and binding, declared
tool schemas, response wrapping, randomness, settings, and the compatibility details
above. No behavioral tests or live RisuAI checks were run for these reference files.

The next implementation step is to establish and test the current host's invocation
path, then add a thin versioned tool adapter with fake-transport tests for valid rolls,
clamped/malformed/error responses, and ambiguous execution. Before enabling it live,
verify restart/result reuse and the existing ten-regeneration acceptance test with no
additional tool calls. This review does not mark that integration as implemented.
