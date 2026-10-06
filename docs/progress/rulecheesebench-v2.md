# RuleCheeseBench v2: runner, validators, drivers, reports

Date: 2026-10-06 (Asia/Seoul).
Status: implemented and tested offline; no live model run yet. See
[the spec](../RULECHEESEBENCH_SPEC.md) for the artifact and outcome contract and
[ADR 0003](../adr/0003-agent-benchmark-reuse.md) for why the runner owns nothing but the
scenario, dice policy, driver and validators.

## Changed

- Scenario schema v2 (`benchmark/models.py`): pinned ruleset (validated against the registry
  the run uses, imports included), public setup text, `seed` or `tape` dice, turn and
  attempt budgets, declarative validators, success/failure lists.
- `TapeDice`: pre-recorded raw results per expression so every driver faces the same physical
  outcomes; an exhausted tape aborts the run instead of silently diverging.
- Validators: actor fields, state paths (a missing path never matches), event counts and
  order, illegal attempts, records.
- Runner: drivers see `Observation` only (setup, command list, budgets, rendered display,
  last command and its result or rejection); rejected commands are counted, shown back, and
  never journaled. Outcome: failure if any failure validator fails, success if the non-empty
  success list passes, otherwise partial (unscored).
- Artifact `BenchmarkRun` v2: versions, policy, initial world, full journal, event ids, final
  state and hash, metrics, verdicts, outcome, driver info, notes. A replay of the artifact's
  journal reproduces its final state (tested).
- Drivers: `ScriptedDriver`; `PromptDriver` with template `rulecheese-prompt-v1`;
  `VertexDriver` (Gemini through the narrator's bounded path; provider imports are lazy;
  failures become sanitized `ValueError`s, never retried).
- Report: `python -m saga_trpg.benchmark report <artifacts…>` refuses runs that differ in
  scenario, ruleset, runtime or dice policy and renders one Markdown table otherwise.
- `benchmarks/scenarios/demo_cheese.json` rewritten for the SRD rulebook.

## Verification

- `pytest` 547 passed; demo: a careful script (`inflict guard prone`, three attacks) scores
  `success` on all five validators, a sloppy one (`dance`, no prone) `failure` with one illegal
  attempt, both on the same seed, rendered side by side.

## Unresolved

- No live Vertex run (each decision is a billed call; operator's call).
- Judges, cheese-taxonomy labelling and memory ablations (the last needs a live NMOS).
- Benchmarks run only on data rulebooks; hand-written Python versions have no command grammar.
