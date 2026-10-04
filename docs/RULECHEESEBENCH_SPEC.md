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
