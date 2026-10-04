# RuleCheeseBench Multi-Agent research spike

These are experimental fixtures, not stable benchmark definitions or a deployed multiplayer
service. See [the experiment report](../../docs/experiments/multiplayer-rulecheese.md).

- `distributed-knowledge.json`: A knows the inscription, C knows the trap warning, B owns the key.
- `cooperative-cheese.json`: A has a hammer, C has a lever; legal bracing improves force by four.
- `bad-teammate.json`: the distributed-knowledge fixture with A's first scripted decision trying
  an invented key; the party then corrects the proposal and shares the required clues.
- `runs.json`: five complete, **judge-only** run artifacts, a JSON array of `RunArtifact` objects.
- `comparison.json`: measured outcomes derived from those artifacts.

All fixtures and artifacts contain private state and validator inputs. Never mount this directory
as an MCP player resource or insert an artifact into a model observation.

After installing the project environment described in the root README, run on Linux/macOS:

```bash
experiment_dir=$(mktemp -d)
.venv/bin/python -m saga_trpg.benchmark.multiagent \
  --scenario benchmarks/multiagent/cooperative-cheese.json \
  --strategy cooperative \
  --database "$experiment_dir/world.sqlite3" \
  --output "$experiment_dir/run.json"
```

Use `--strategy brute_force` for the seven-turn comparison, or `--strategy silent` with the
distributed-knowledge fixture for the no-sharing failure. The bad-teammate fixture selects the
one-invalid-proposal script for A. Use fresh output/database paths for each run; the CLI rejects
overwriting existing research data. Exit zero means a run artifact was produced, not that the
party won. Inspect `validator.legal_success` separately.

No paid model, network, MCP server, frontend, NMOS installation, or external database is needed.
The declared human participant is also scripted in this harness. Unknown token usage is `null`.

```bash
.venv/bin/pytest -q tests/test_multiplayer.py tests/test_multiagent_benchmark.py
```
