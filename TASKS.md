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
- [ ] RisuAI request audit
- [ ] RisuAI world/session mapping
- [ ] rerender detection
- [ ] intent interpreter
- [ ] narrator integration

## P2 — Tools + rules

- [ ] MCP dice adapter
- [x] durable generic tool-result tape, external dice integration seam, restart/concurrency tests
- [ ] provider-specific reconciliation of ambiguous executions before enabling live MCP tools
- [ ] inventory module
- [ ] checks/saves module
- [ ] conditions module
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
