# ADR 0005: Durable external tool results and explicit storage upgrades

Status: accepted

## Problem

An external tool can execute before a world transaction fails. Rolling back the journal
does not roll back a remote side effect or random result. Deterministic local dice alone
cannot establish the retry contract for an eventual MCP client.

## Decision

Keep all persistence and remote I/O in adapters. Add a transport-neutral `ToolResultTapePort`
with versioned JSON calls/results, implemented with SQLite in the same database as worlds.
The primary identity is world/branch/event/call key; compare canonical hashes of arguments
and the complete request, including tool/protocol versions and optional invocation context.
Pass a stable request-derived remote idempotency key to the executor.

The durable state transitions are `absent → pending → completed → committed`. The pending
claim commits before remote execution. Completion commits before a result is consumed by
rules. The final event transaction atomically marks the tape and event reservation committed.
Uniqueness and transaction locks coordinate processes; process-local locks are insufficient.
An existing pending claim is never reacquired, expired, or automatically executed again.

The repository's `RecordedDice` reads results through the existing `DicePort`. A missing
result suspends a speculative rule attempt. The outer repository reserves that event and
its input state, commits the reservation, executes via the tape, and repeats evaluation
using recorded results. This avoids holding a database transaction during remote execution
and keeps filesystem/network/wall-clock operations out of deterministic code. A branch
with a reserved event rejects other mutations. Reads and forks of committed prefixes work.
Rules must remain stateless and deterministic; their partial output is never published.

The generic tape also exposes direct `execute`/`get` methods for future non-dice integrations.
They provide call-level persistence, not automatic event orchestration. Such integrations
must define typed consumers and reserve/commit events through the outer boundary just as
the current dice path does; no new game mechanics or generic rule dispatcher is added here.

## Migration

Storage v2 adds two tables and a partial unique index to real storage v1. The explicit CLI
backs up through SQLite's backup API, validates history and snapshots, executes ordered DDL
in one transaction, and verifies unchanged replay hashes and original row values. Failures
roll back schema/data/version. Current data is a no-op; unknown versions fail closed.
State/event/journal schema v1 and runtime version `0.2.0` retain their semantics. No history
rewrite, JSONL import, or hypothetical historical state conversion is introduced.

## Trade-offs and limits

- Exactly-once remote execution needs remote cooperation. A timeout or crash between claim
  and result persistence leaves an intentionally ambiguous record; even a crash before
  actually sending the request cannot safely be inferred from the database alone.
- The initial adapter exposes no reset/reconcile operation. Before enabling a real remote,
  implement provider-specific receipt/status/idempotency reconciliation with audit evidence.
- Multiple rule passes cost CPU and a pending event blocks its branch. This is acceptable
  for current small stateless rules and avoids speculative concurrent state semantics.
- All results/history are retained. Large generic payloads, pagination, retention, and a
  broader tool capability model are deferred.
- Back up the whole database. Restoring older local data cannot undo remote side effects.
- Bare injected `DicePort` remains available for pure deterministic/test implementations;
  callers that bypass the tape with remote code violate the supported integration contract.
- Forks retain journaled committed rolls without duplicating pending tool executions.

## Evidence

Focused tests exercise the real repository/tape with SQLite, separate processes, explicit
process exits, transaction failures, changed arguments/proposals, fork scopes, and repeated
narration. Remote transports are fake; no real MCP exactly-once claim is made. The migration
fixture was exported by the prior v1 implementation before the storage change.
