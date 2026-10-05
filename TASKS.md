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

- [ ] current NMOS API audit
- [ ] NMOS adapter
- [ ] actor visibility model
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
- [ ] inventory module
- [x] checks/saves module (SRD)
- [x] conditions module (SRD `0.2.0`: explicit apply/remove, no effects)
- [x] damage/heal and simulated time (SRD `0.2.0`)
- [ ] PocketRisu bridge and commands on SRD `0.2.0`
- [ ] quest module
- [ ] observation/knowledge module

## P3 — RuleCheeseBench

- [ ] scenario loader
- [ ] hidden validators
- [ ] model driver
- [ ] fixed stochastic conditions
- [ ] run artifacts
- [ ] report generator
- [ ] NMOS ablation suite
