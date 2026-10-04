# AGENTS.md

This repository is designed for agent-driven implementation. Preserve the architecture before adding features.

## Primary objective

Build a long-horizon, replayable TRPG runtime that can sit behind RisuAI and use NMOS as semantic memory. The runtime must also serve as the execution substrate for RuleCheeseBench.

## Hard architecture rules

- `domain/` and the deterministic parts of `runtime/` MUST NOT perform network I/O, filesystem I/O, wall-clock reads, or unseeded randomness.
- NMOS is accessed only through `MemoryPort`.
- Dice/tool execution is accessed only through tool ports such as `DicePort`.
- An LLM NEVER mutates `WorldState` directly.
- Any LLM-derived intent must first become a validated `GameEvent`.
- Exact game facts live in authoritative state, not NMOS.
- Do not copy NMOS internals into this repository. Implement an adapter to the public/local NMOS boundary that exists at implementation time.
- Do not build a second vector store, embedding index, or memory-consolidation subsystem here unless a benchmark-specific fake is required.
- Rerender/reroll MUST reuse journaled state transitions and roll records. Never call dice/MCP again when only prose is regenerated.
- Every committed mutation must have an event id and trace.

## Work style

1. Read `docs/ARCHITECTURE.md` and `docs/AGENT_HANDOFF.md` before modifying architecture.
2. Make the smallest coherent vertical slice.
3. Add or update tests for invariants before refactors.
4. Keep adapters thin. If an adapter starts containing game logic, move the logic inward.
5. Prefer typed schemas and explicit version fields over ad-hoc dictionaries.
6. Keep benchmark code separate from production orchestration, but reuse the same runtime.
7. Do not silently change event semantics. Add schema/runtime versioning when semantics change.

## Definition of done for implementation tasks

- tests pass
- deterministic replay still passes
- rerender does not mutate world/journal
- failure behavior is documented
- no secret/API key added to repo
- public contract changes are reflected in docs and schemas
- self-review completed against the hard architecture rules above

## Commands

```bash
pytest
python -m compileall src
ruff check .
ruff format --check .
```

## Priority order

P0: deterministic runtime + journal + replay correctness
P1: NMOS adapter + RisuAI orchestration
P2: tool/MCP adapters and modular rulesets
P3: RuleCheeseBench automation and reporting
P4: authoring UI / convenience features
