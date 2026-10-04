# Implementation boundaries

The project is **SAGA — Stateful Agentic Game Architecture**, distributed as `saga-trpg`
with Python package `saga_trpg`. SQLite provides persistent worlds, snapshots, event history,
and the external tool-result tape. Explicit storage upgrades preserve replayable history.

SRD 5.2.1 and Fate Condensed are licensed, partial ruleset examples; see
[the license notes](docs/RULESET_LICENSES.md) for their scope and attribution.

Still intentionally unselected or unimplemented:

- a concrete MCP server/client and provider-specific execution reconciliation;
- an LLM intent interpreter (the PocketRisu demo accepts closed SRD commands only);
- the live NMOS API binding (semantic memory remains optional);
- narration streaming, general RisuAI card/lore compatibility, and the full benchmark runner.

The PocketRisu command bridge and opt-in Vertex narration after commit are implemented; see
[the integration contract](docs/RISUAI_INTEGRATION.md).

The filename is retained as a pointer from early checkouts. These notes describe the current
implementation; historical reports under `docs/progress/` retain their original time context.
