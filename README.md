# SAGA — Stateful Agentic Game Architecture

[한국어](README.ko.md) | **English**

[![CI (main)](https://github.com/Sallos725/SAGA/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Sallos725/SAGA/actions/workflows/ci.yml)
[![Python 3.11–3.13](docs/assets/python-versions.svg)](pyproject.toml)
[![Stage: prototype](docs/assets/stage-prototype.svg)](docs/IMPLEMENTATION_PLAN.md)

A deterministic, replayable TRPG runtime for long-horizon agent-driven games and RisuAI.

[Quick start](#quick-start) · [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/IMPLEMENTATION_PLAN.md) · [Rulesets](packs/rulesets/README.md) · [Contributing](CONTRIBUTING.md)

The design goal is not to build another prompt-heavy chatbot. It is to split responsibilities cleanly:

- **Runtime / game state** owns authoritative facts: HP, inventory, positions, conditions, quest state, clocks, and event ordering.
- **Semantic memory** is optional behind `MemoryPort`: use no memory, a fake adapter, NMOS,
  or another implementation for recall and retrieval.
- **Dice / tools** own calculations and externally verifiable tool results.
- **LLM** interprets constrained intent and narrates already-decided outcomes. It never directly mutates authoritative state.
- **RisuAI** is a frontend and card/lore delivery surface, not the source of truth.
- **RuleCheeseBench** reuses the exact same runtime to compare models under replayable scenarios.

SAGA provides persistent state, replay, branching, and durable external tool results.
Product-specific NMOS, MCP, and RisuAI bindings remain separate integration work.

## Why this shape

SAGA borrows one strong idea from deterministic RP engines such as Aethrion: a reroll may
change prose, but it must not change state or dice. Long-horizon memory is an optional external
adapter; SAGA does not embed a second memory system into the game engine.

## Non-negotiable invariants

1. **Authoritative state is not memory.** Do not ask NMOS to remember exact HP, inventory counts, quest stages, or other values that must be exact.
2. **Reroll does not execute tools twice.** Dice/tool outputs are journaled once and replayed.
3. **Every committed state mutation is traceable to an event.**
4. **Core rules do not read wall-clock time or perform I/O.** Time enters as explicit events.
5. **LLM output is not trusted state.** Parsed intents must be validated against a closed schema before dispatch.
6. **Memory failures are fail-soft.** A temporary NMOS outage may reduce recall quality but must not corrupt game state.
7. **Benchmark runs are reproducible.** Scenario, rule version, seed, model config, and event journal are captured.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
python -m pip install -e '.[dev,api]'
pytest
uvicorn saga_trpg.api.app:create_app --factory --reload --port 4850
```

Then try:

```bash
curl -s -X POST http://127.0.0.1:4850/worlds/demo/events \
  -H 'content-type: application/json' \
  -d '{"event_id":"turn-1","kind":"attack","actor_id":"player","payload":{"target_id":"goblin","attack_bonus":5,"target_ac":14,"damage":"1d8+3"}}'

curl -s http://127.0.0.1:4850/worlds/demo/state
```

The debug API now persists worlds to `./data/worlds.sqlite3` (`TRPG_DATA_DIR` overrides the
directory). Repeating a request with the same event id or idempotency key returns its recorded
result. Use a new identity for a new action. See [the persistence contract](docs/PERSISTENCE.md)
for restart recovery, snapshots, replay, branching, and failure behavior.

The distribution is `saga-trpg`; Python imports use `saga_trpg`. Reinstall the editable package
after upgrading an older checkout. No import alias for the former package is provided.
Existing storage v1 databases need an explicit, backed-up upgrade before starting the host:

```bash
python -m saga_trpg.adapters.storage.migrations data/worlds.sqlite3 \
  --backup data/worlds.pre-v2.sqlite3
```

Stop the host first and choose a new backup filename. Current v2 databases are a no-op;
unknown versions fail closed. Event/state schema v1 and runtime semantics remain unchanged.

Use Python 3.11–3.13 and the same verification commands locally as CI:

```bash
pytest
ruff check .
ruff format --check .
python -m compileall src
```

GitHub Actions runs the complete suite on all three versions for pushes and pull requests.

Two selectable system examples are available: **SRD 5.2.1** (CC BY 4.0) and **Fate Condensed**
(CC BY 3.0). Each includes typed action handlers, a module manifest, the shared bot description,
a system prompt, sample actors/actions, and attribution. See
[the ruleset examples](packs/rulesets/README.md) for creation commands and exact supported scope.
These are partial runtime/source-pack examples; native RisuAI module importing is not yet wired.

## Project map

```text
src/saga_trpg/
  domain/        Pure data models
  runtime/       Event dispatch, journal, replay, deterministic RNG
  ports/         Interfaces for memory, dice, narration, intent
  adapters/      NMOS, local/MCP dice, RisuAI/OpenAI-compatible adapters
  modules/       Pluggable rulesets
  benchmark/     RuleCheeseBench harness
  api/           Thin HTTP host

docs/
  ARCHITECTURE.md
  IMPLEMENTATION_PLAN.md
  AGENT_HANDOFF.md
  RULECHEESEBENCH_SPEC.md
  RISUAI_INTEGRATION.md
  adr/
```

## Current implementation

Implemented:

- event/state models
- deterministic dice keyed by world + event + roll key
- in-memory/JSONL journals and a transactional SQLite world repository
- periodic snapshots, verified replay, and independent branches with fork provenance
- persistent idempotency by event id or idempotency key
- explicit storage v1 → v2 migration with backup, rollback, and replay validation
- generic SQLite tool-result tape and a repository path for taped external dice results
- selectable, versioned SRD 5.2.1 and Fate Condensed example modules with closed action schemas
- replayable generic-fantasy attack rule
- FastAPI debug endpoints
- NMOS and MCP Dice ports/adapters with explicit TODO contracts
- benchmark result models and scenario shape
- tests for the most important invariants

Not implemented yet:

- production NMOS API binding
- production MCP client binding
- LLM intent interpreter
- RisuAI `/v1/chat/completions` orchestration
- legacy JSONL import and future state/event semantic migrations
- complete TRPG rulesets
- full RuleCheeseBench runner and judges

Start with `docs/AGENT_HANDOFF.md` if you are handing this repo to Codex or Claude Code.

## Safety / deployment note

Keep local credentials in `.env` and saved campaigns under `data/`. Git and Docker build
exclusions cover local environment files, private keys, databases and their sidecar files,
journals, backups, and local caches. `.env.example` contains public placeholders and remains
included. Supply configuration at runtime; it is not baked into the image.

The debug HTTP host has no production authentication or multi-tenant isolation yet. Bind it to localhost/private networking until the outer API/auth layer is implemented and reviewed.

The future user-facing RisuAI bundle lives under `packs/risu-starter/`; its exported card/lore artifacts are intentionally not fabricated until the current RisuAI format is verified.

When changing setup instructions, supported features, or limitations, update both
`README.md` and `README.ko.md` in the same change.
