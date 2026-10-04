# ADR 0003: RuleCheeseBench reuses the production runtime

Status: Accepted

## Decision

The benchmark harness drives the same event/rule/tool/journal runtime used for interactive play.

## Rationale

A separate benchmark simulator would drift from real behavior and make results less meaningful.

## Consequence

Benchmark-only code may control scenarios, seeds, model drivers, and validators, but must not implement a second rules engine.
