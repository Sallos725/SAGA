# ADR 0004: Optional performance is a projection of narration

Status: Proposed; local foundation implemented, host integration deferred

Date: 2026-10-04

## Decision

Pursue a small performance foundation now; schedule providers and host integration after
the RisuAI turn-identity/narration slice. Keep performance outside `domain/`, game dispatch,
state hashes, snapshots, mechanical tool execution, and deterministic replay.

A committed result may have multiple narration renditions. Each rendition has its own
caller-assigned identity. Completed text and optional structured dialogue spans produce
versioned presentation events, referencing canonical actor ids from the committed snapshot.
Unresolved speakers remain unresolved. Invalid or missing annotations preserve ordinary text.
No new narrator requirements, network calls, database migration, or frontend protocol are
part of the foundation.

Prefer a hybrid: explicit attribution when available, deterministic validation/resolution,
then optional inference for ambiguity and delivery. A schema constrains output shape; it
does not establish that an inferred speaker or emotion is correct.

## Consequences

- `GameEvent` remains the only game mutation proposal. Dialogue and performance events are
  not rule inputs or evidence that a fictional statement changed the world.
- Narrative regeneration, performance rebuilding, mechanical replay, and replaying saved
  media are separate operations. Only an explicit rebuild may call presentation providers.
- Consumers deduplicate stable performance identities; this foundation does not promise
  durable delivery or exactly-once external effects.
- Consumer failures and deadlines return presentation status without changing text or
  invalidating the already committed turn. Cancellation still propagates.
- Raw narration/media need a separate, opt-in presentation archive before reproducible
  audiovisual replay can be promised. The current mechanical tool tape is not that archive.

See [the investigation and implementation plan](../PERFORMANCE_LAYER.md) for evidence,
schemas, streaming, privacy, failure policies, and acceptance gates.
