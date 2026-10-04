# ADR 0002: Commit tool outcomes before narration

Status: Accepted

## Decision

Dice/tool results and state transitions are committed to the event journal before narration is generated.

## Rationale

Narration may be retried, regenerated, timed out, or changed between models. Game outcomes must not depend on prose generation retries.

## Consequence

A narration rerender always reads a committed result and never re-executes dice/tools.
