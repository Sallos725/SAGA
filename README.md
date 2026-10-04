# NMOS TRPG Runtime Scaffold

A deliberately small, agent-friendly scaffold for a long-horizon RisuAI TRPG backend.

The design goal is not to build another prompt-heavy chatbot. It is to split responsibilities cleanly:

- **Runtime / game state** owns authoritative facts: HP, inventory, positions, conditions, quest state, clocks, and event ordering.
- **NMOS** owns long-horizon semantic memory: what happened, who knows it, which facts matter now, and retrieval.
- **Dice / tools** own calculations and externally verifiable tool results.
- **LLM** interprets constrained intent and narrates already-decided outcomes. It never directly mutates authoritative state.
- **RisuAI** is a frontend and card/lore delivery surface, not the source of truth.
- **RuleCheeseBench** reuses the exact same runtime to compare models under replayable scenarios.

This repo is a scaffold. It is intentionally opinionated about boundaries and intentionally incomplete about product-specific adapters.

## Why this shape

The scaffold borrows one strong idea from deterministic RP engines such as Aethrion: a reroll may change prose, but it must not change state or dice. It differs by making long-horizon memory an external NMOS adapter rather than embedding a second memory system into the game engine.

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
pip install -e '.[dev,api]'
pytest
uvicorn nmos_trpg.api.app:create_app --factory --reload --port 4850
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

Two selectable system examples are available: **SRD 5.2.1** (CC BY 4.0) and **Fate Condensed**
(CC BY 3.0). Each includes typed action handlers, a module manifest, the shared bot description,
a system prompt, sample actors/actions, and attribution. See
[the ruleset examples](packs/rulesets/README.md) for creation commands and exact supported scope.
These are partial runtime/source-pack examples; native RisuAI module importing is not yet wired.

## Project map

```text
src/nmos_trpg/
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

## Current scaffold status

Implemented enough to establish boundaries and run a deterministic demo:

- event/state models
- deterministic dice keyed by world + event + roll key
- in-memory/JSONL journals and a transactional SQLite world repository
- periodic snapshots, verified replay, and independent branches with fork provenance
- persistent idempotency by event id or idempotency key
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
- automatic storage migrations and legacy JSONL import
- complete TRPG rulesets
- full RuleCheeseBench runner and judges

Start with `docs/AGENT_HANDOFF.md` if you are handing this repo to Codex or Claude Code.

## Safety / deployment note

Keep local credentials in `.env` and saved campaigns under `data/`. Git and Docker build
exclusions cover local environment files, private keys, databases and their sidecar files,
journals, backups, and local caches. `.env.example` contains public placeholders and remains
included. Supply configuration at runtime; it is not baked into the image.

The debug HTTP scaffold has no production authentication or multi-tenant isolation yet. Bind it to localhost/private networking until the outer API/auth layer is implemented and reviewed.

The future user-facing RisuAI bundle lives under `packs/risu-starter/`; its exported card/lore artifacts are intentionally not fabricated until the current RisuAI format is verified.
