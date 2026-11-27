# Tasks

Current product direction and milestone scope (**M1–M3 + A2** implemented and verified offline;
NMOS live/visibility, MCP external dice, live RuleCheeseBench runs, and Presentation/TTS
explicitly deferred) are recorded in [docs/DIRECTION.md](docs/DIRECTION.md).

Public issue tracking uses [GitHub Issues](https://github.com/Sallos725/SAGA/issues) (see
[docs/ISSUE_TRACKING.md](docs/ISSUE_TRACKING.md)). The checklist below is a repository summary
and can lag newer progress reports in `docs/progress/`.

## P0 — Runtime correctness

- [x] persistent world repository (SQLite)
- [x] snapshot policy
- [x] replay verification from genesis/snapshot
- [x] branch/fork support
- [x] event schema registry for the SRD/Fate example modules
- [x] crash consistency
- [x] minimal migration framework: explicit storage v1 → v2, backup, atomic rollback, replay checks
- [x] SAGA identity/package rename and Python 3.11–3.13 CI

## P1 — RisuAI play foundation & NMOS

- [x] current NMOS API audit (`docs/NMOS_SIDECAR.md`, sidecar 0.3.0)
- [x] NMOS adapter (fail-soft, stateless; bridge writes facts after commit and recalls before narration)
- [ ] actor visibility model (deferred until long-campaign NMOS round trip)
- [ ] live NMOS round trip on a sidecar the operator designates (deferred)
- [x] RisuAI request audit and world/session mapping with rerender detection
- [x] closed deterministic command interpretation
- [x] state-neutral `narrative_turn` (no-op) event and protocol v4 (`POST /v1/saga/turn`)
- [x] OpenAI-compatible narrator & grounded rulebook intent adapter (`risu_prompt`, SSE streaming, flexible reasoning/header/body params)
- [x] RisuAI Hook mode (`beforeRequest` / `afterRequest`) + Provider mode dual-mode plugin (`v0.11.2`) with fullscreen settings/status panel and UI-configurable Intent LLM (`runLLMModel` submodel + OpenAI-compatible override)
- [x] scenario cards (`ScenarioSpec` v1) + multi-format Card-to-Scenario converter (`.charx`/`.png`/`.json`, CLI, API, standalone HTML webapp `tools/saga-converter.html`, in-plugin panel)
- [x] opt-in Vertex natural-language intent & narration after commit, with fallback
- [ ] live browser/host E2E verification of M1–M3 + A2 (Hook mode, streaming, card converter, scenario play)

## P2 — Tools + rules

- [ ] MCP dice adapter (deferred)
- [x] durable generic tool-result tape, external dice integration seam, restart/concurrency tests
- [ ] provider-specific reconciliation of ambiguous executions before enabling live MCP tools (deferred)
- [x] inventory, checks/saves, conditions, damage/heal, simulated time modules
- [x] rulebook schema v1 + formula language + generic engine; SRD `0.6.0` and Fate Condensed `0.5.0` as data
- [x] condition effects on rolls and record builtins (adjust/remove)
- [x] RisuAI module export (`.json`, `.risum`, `.charx`) + backend import/pinning
- [x] room-graph maps, `move <exit>`, Map v2 (locked exits, loot, room inspection/clues, NPC movement)
- [x] quest tracking (`quest_update`), free-action GM adjudication (`adjudicate_action`), append-only GM correction (`gm_correction`), and dynamic in-play `register_actor` / `add_room`
- [x] authoritative B1 Turn Cards and B2 Scene Board projections
- [ ] observation/knowledge module (actor-scoped knowledge beyond room visibility)

## P3 — RuleCheeseBench

- [x] scenario loader (schema v2)
- [x] hidden validators
- [x] model driver (prompt template v1, Vertex)
- [ ] first live Vertex benchmark run and its report (deferred)
- [x] fixed stochastic conditions (seed / tape)
- [x] run artifacts
- [x] report generator (comparability-checked Markdown table)
- [ ] NMOS ablation suite (deferred)

## P4 — Authoring

- [x] "rulebook author" skill for AI-assisted creators, on top of `docs/RULEBOOKS.md`
- [x] standalone visual Card-to-Scenario converter (`tools/saga-converter.html` / `GET /converter`)
- [ ] rulebook template generator (`saga_trpg.rulebooks new`)
