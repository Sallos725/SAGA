# Tasks

## P0 — Runtime correctness

- [x] persistent world repository
- [x] snapshot policy
- [x] replay verification from genesis/snapshot
- [x] branch/fork support
- [x] event schema registry for the new SRD/Fate example modules
- [x] crash consistency
- [x] minimal migration framework: explicit storage v1 → v2, backup, atomic rollback, replay checks
- [x] SAGA identity/package rename and Python 3.11–3.13 CI

## P1 — NMOS + RisuAI

- [x] current NMOS API audit (`docs/NMOS_SIDECAR.md`, sidecar 0.3.0)
- [x] NMOS adapter (fail-soft, stateless; bridge writes facts after commit and recalls before narration)
- [ ] actor visibility model (sidecar has no actor-scoped retrieval yet)
- [ ] live NMOS round trip on a sidecar the operator designates
- [x] RisuAI request audit (pinned PocketRisu 1.13.0 provider subset)
- [x] RisuAI world/session mapping
- [x] rerender detection
- [x] closed deterministic command interpretation
- [x] bounded Vertex natural-language attack/check/save interpreter, existing durable tape (offline verified)
- [ ] live natural-language quality and 6011 upgrade verification
- [x] opt-in Vertex narration after commit, with fallback
- [ ] narration streaming and general card/lore compatibility

## P2 — Tools + rules

- [ ] MCP dice adapter
- [x] durable generic tool-result tape, external dice integration seam, restart/concurrency tests
- [ ] provider-specific reconciliation of ambiguous executions before enabling live MCP tools
- [x] inventory module (SRD `0.3.0`: counts only, no effects)
- [x] checks/saves module (SRD)
- [x] conditions module (SRD `0.2.0`: explicit apply/remove, no effects)
- [x] damage/heal and simulated time (SRD `0.2.0`)
- [x] rulebook schema v1 + formula language + generic engine; SRD `0.4.0` as data
- [x] condition effects on rolls from the conditions table
- [x] Fate Condensed as a rulebook (graded outcomes, pending adjudication, records)
- [x] RisuAI module export + backend import/pinning for rulebooks
- [x] plugin forwards module rulebook; rulebook-derived commands in the bridge
- [x] `.risum` / `.charx` export (RPack table supplied, not vendored)
- [x] rulebook `display` section driving the plugin footer
- [x] record builtins (adjust/remove) for Fate invokes and boosts
- [x] PocketRisu bridge and commands on SRD `0.2.0` (new worlds; existing `0.1.0` worlds unchanged)
- [ ] quest module
- [ ] observation/knowledge module

## P3 — RuleCheeseBench

- [x] scenario loader (schema v2)
- [x] hidden validators
- [x] model driver (prompt template v1, Vertex)
- [ ] first live Vertex benchmark run and its report
- [x] fixed stochastic conditions (seed / tape)
- [x] run artifacts
- [x] report generator (comparability-checked Markdown table)
- [ ] NMOS ablation suite

## P4 — Authoring

- [x] "rulebook author" skill for AI-assisted creators, on top of `docs/RULEBOOKS.md`
- [ ] rulebook template generator (`saga_trpg.rulebooks new`)
