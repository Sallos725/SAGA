# RuleCheeseBench vNext — Skeleton Spec

## Goal

Measure how well a model can understand, obey, combine, and creatively exploit a known ruleset over a long-horizon interactive scenario without rewarding illegal moves or accidental state drift.

## Core principle

A benchmark run is not "give several models similar prompts and eyeball the prose." It is:

```text
same scenario definition
same runtime/ruleset version
same initial state
same allowed information
same stochastic policy
model-specific decisions
-> inspectable event journal
-> deterministic validators
```

## Candidate metric families

These are dimensions, not a final weighted leaderboard design.

- Rule comprehension: valid action rate, correct precondition use
- Constraint adherence: illegal action attempts, hallucinated resources
- Exploit discovery: discovers permitted non-obvious interactions
- Planning depth: achieves delayed prerequisites before payoff
- State tracking: decisions consistent with authoritative state
- Long-term recall: uses distant but retrievable clues appropriately
- Epistemic discipline: does not act on information unavailable to the actor
- Recovery: revises plan after failure without inventing state
- Efficiency: objective progress per action/token/tool call
- Robustness: performance under irrelevant distractor memories

## Cheese taxonomy proposal

Do not treat "more cheese" as automatically better. Record type and legality separately.

- C0: ordinary intended use
- C1: efficient combination of obvious rules
- C2: non-obvious but clearly intended synergy
- C3: edge-case interaction that is legal under written rules
- C4: rules-lawyer exploit that strongly departs from apparent design intent but remains legal
- C5: engine/spec ambiguity requiring adjudication
- INVALID: violates explicit rules or invents unavailable state

C5 should normally trigger scenario review rather than reward the model.

## Scenario artifact must include

- scenario id/version
- ruleset id/version
- initial authoritative state
- public setup text
- hidden validator state
- available action schema/tools
- stochastic mode
- max turns / resource budget
- success conditions
- failure conditions
- optional memory injections/distractors

## Implemented (2026-10-06): scenario v2, scripted driver, validators, artifacts

- `benchmarks/scenarios/*.json` (schema v2, `saga_trpg.benchmark.models`): pinned
  `ruleset_id`/`ruleset_version` (must be registered), `initial_world`, public `setup_text`,
  `stochastic` (`seed` → seeded deterministic dice; `tape` → pre-recorded raw results per
  expression so different policies face identical physical outcomes), `max_turns`,
  declarative `validators` and the `success` / `failure` lists.
- Validators (`benchmark/validators.py`): `actor` field comparisons, `state` paths,
  `event_count`, `event_order`, `illegal_attempts`, `record`. They read only the final
  state, the journal and the illegal-attempt count.
- Runner (`benchmark/rulecheese.py`): a `Driver` sees an `Observation` (setup text, the
  rulebook's command list, turn budget, rendered display, last facts/error) and returns a
  command; the runner parses it with the rulebook grammar and dispatches through the
  production `RuntimeEngine`. A rejected command never reaches the journal, counts as an
  illegal attempt and is shown to the driver as an error. `ScriptedDriver` plays a fixed
  list. `benchmark/drivers.py`: `PromptDriver` renders the observation with
  `rulecheese-prompt-v1` (setup text, budgets, rendered display, last command and its result
  or rejection, the command list) and takes the last meaningful line of any async completion
  as the command; `END` stops early. `VertexDriver` runs it through the narrator's bounded
  Vertex path (same service-account JSON, Gemini). The artifact records the driver name,
  template version, call count and early stop.
- Artifact (`BenchmarkRun` v2): scenario and ruleset versions, runtime version, stochastic
  policy, initial world, full journal, event ids, final state and hash, metrics (turns,
  attempts, illegal attempts, valid-action rate, rolls), validator verdicts, outcome
  (`success` / `partial` / `failure`) and notes. `python -m saga_trpg.benchmark run` writes it.
- Outcome: `failure` when any `failure` validator fails; `success` when `success` is non-empty
  and all of it passes; otherwise `partial` (a scenario without success criteria is unscored).
- Budgets: `max_turns` bounds committed turns and `max_attempts` (default 2 × `max_turns`)
  bounds decisions including rejected ones; both remaining budgets are in the observation.
- Hidden validator state and the success/failure lists are never part of the observation.

- Report (`benchmark/report.py`, `python -m saga_trpg.benchmark report`): refuses artifacts
  that differ in scenario, ruleset, runtime or dice policy; renders one Markdown table
  (outcome, turns, attempts, illegal attempts, valid-action rate, rolls, verdicts, driver,
  final state hash).

Not implemented: judges, memory ablations, cheese taxonomy labelling, drivers for other
providers. No live Vertex benchmark run has been made yet.

## Long-horizon NMOS experiments

Useful controlled comparisons:

- no external memory
- bounded recent context
- periodic summary only
- NMOS global retrieval
- NMOS actor-scoped retrieval
- NMOS with distractor memories
- NMOS retrieval disabled mid-run (degradation test)

The runtime state must remain identical in ownership across these conditions. Only information availability changes.

## Reproducibility bundle

Each run should save:

- scenario JSON/YAML
- runtime commit/version
- ruleset version
- model/provider/config
- prompts/templates version
- seed or pre-recorded roll tape
- full event journal
- selected memory retrieval ids/scores/metadata
- tool calls/results
- validator outputs
- final state hash
