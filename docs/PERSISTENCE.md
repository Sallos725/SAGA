# Persistent runtime contract

The first implementation slice uses `SQLiteWorldRepository`, an outer I/O adapter around
the same deterministic `RuntimeEngine`. SQLite is in Python's standard library. No service,
new package dependency, NMOS binding, or external tool client is required.

## Storage and transactions

The debug HTTP host stores worlds at `$TRPG_DATA_DIR/worlds.sqlite3`; the default directory
is `./data`. `TRPG_DEFAULT_SEED` applies when a world is created. Each world stores its seed,
initial state, ruleset name/version, and runtime version; changing the environment seed does
not change an existing world's future rolls. An explicitly injected `DicePort` overrides the
reference dice for that repository instance and is the caller's reproducibility responsibility.

The default repository resolves each world's stored ruleset id/version through the built-in
registry; different campaigns can use different systems in the same database. Explicitly
injected rule implementations remain restricted to their exact name/version for tests or hosts
that need them. The database schema is unchanged. `POST /worlds/{world_id}` selects a registered
system and creates its example world; existing implicit demo creation still uses generic-fantasy.
See [the ruleset examples](../packs/rulesets/README.md) for the public catalog and context routes.

Each dispatch opens a connection, starts `BEGIN IMMEDIATE`, loads the current branch state,
checks event identity, executes rules through the runtime, inserts the journal entry and any
due snapshot, and commits. The adapter returns the result only after commit. SQLite serializes
writes across threads and processes, including the idempotency check and tool execution.
The connection uses foreign keys, `synchronous=FULL`, and a five-second lock timeout. Database
initialization is also transactional. Use a local filesystem with SQLite-compatible locking.

The database contains four tables:

| Table | Stored data |
| --- | --- |
| `worlds` | Initial state, seed, ruleset/runtime compatibility metadata |
| `branches` | Branch id, parent branch, fork event id and sequence |
| `entries` | Committed event/result, trace, roll records, before/after state hashes |
| `snapshots` | World state and hash at a committed sequence |

Application code only inserts history and snapshots; it does not rewrite or truncate them.
The initial branch is `main`. A snapshot is saved at genesis and every 50 new committed events
by default (`snapshot_interval` is configurable on the repository constructor), plus at each
fork. Retries do not add snapshots. All history and snapshots are retained; there is no
automatic compaction or deletion policy in this slice.

Every entry currently stores a full post-event state. Normal state recovery validates the
latest snapshot, its corresponding journal result, and the subsequent contiguous transition
chain. Full replay starts at genesis and verifies the entire requested prefix, independent of
snapshots. Neither path invokes rules or dice. Snapshot recovery does not audit earlier history;
use full replay to detect corruption before the snapshot. Hashes detect state-chain corruption,
not cryptographic authenticity of every event, trace, or tool field.

## Idempotency and branching

Supply `event_id`, `idempotency_key`, or both for retryable requests. Their uniqueness is scoped
to a world and branch. Repeating the same proposal returns its original journal entry without
calling rules/tools or rewinding the current state. An old result's `state` is therefore the
state after that event; query `/state` for the current head.

Reusing an identity for a different proposal, supplying inconsistent identities, or colliding
with a generated event id raises `IdempotencyConflict`. Only the identity fields are excluded
from action comparison; schema version, kind, actor, payload, and parent event are compared
using canonical JSON hashing. Boolean and numeric values are distinct. Omitting both identity
fields deliberately creates a new action; prose matching is never used for deduplication.

A fork copies the validated committed prefix through the selected event, including event ids,
idempotency keys, roll records, and hashes. It stores explicit parent/fork provenance and a
snapshot of that state. The source branch is unchanged. Subsequent auto-generated ids include
the new branch id so new branch rolls do not accidentally share the source's generated identity.
Retries of inherited events return the inherited result. Explicit ids remain scoped per branch.
Forks can themselves be forked. A required `at_event_id: null` selects genesis deliberately.

Copies make this implementation simple and independently replayable, at the cost of storage
proportional to the copied prefix. Large histories may later need shared immutable prefixes,
compact patches, indexes, and paginated history reads; none are silently approximated here.

## HTTP surface

Existing state and dispatch endpoints keep their demo-world behavior: a request on `main`
creates a missing world with the player and goblin. Other branches must exist already.
All branch-aware routes accept `branch_id` as a query parameter, defaulting to `main`.

| Method and path | Result |
| --- | --- |
| `GET /worlds/{world_id}/state` | Current state recovered from snapshot and recorded results |
| `POST /worlds/{world_id}/events` | Commit one event or return its recorded duplicate; example modules include structured `resolution` |
| `GET /worlds/{world_id}/events` | Ordered committed history, including traces and roll records |
| `GET /worlds/{world_id}/events/{event_id}` | One committed result for read-only consumers |
| `GET /worlds/{world_id}/replay` | Full replay to head; optional `at_event_id` selects an inclusive prefix |
| `GET /worlds/{world_id}/branches` | Branch metadata and fork provenance |
| `POST /worlds/{world_id}/branches` | Fork with JSON `branch_id`, `at_event_id`, optional `source_branch_id` |

Example after committing event `turn-1`:

```bash
curl -s -X POST http://127.0.0.1:4850/worlds/demo/branches \
  -H 'content-type: application/json' \
  -d '{"branch_id":"alternate","source_branch_id":"main","at_event_id":"turn-1"}'
curl -s 'http://127.0.0.1:4850/worlds/demo/state?branch_id=alternate'
```

Fork returns HTTP 201. Missing worlds/branches/events return 404; identity and duplicate
world/branch conflicts return 409; invalid event envelopes or malformed rule inputs return 422.
Storage errors, replay-integrity failures, and incompatible stored versions return 503 without
exposing a new uncommitted result. Storage details are logged server-side. The legacy
generic-fantasy module retains its traced no-op for unknown actions. New registered example
modules reject unsupported kinds and use closed payload schemas before rolling. The closed
free-text intent interpreter is still future work. These remain private/debug endpoints with
the scaffold's existing deployment boundary.

## Failure and compatibility policy

- SQLite rolls back a failed event/snapshot transaction. A process exiting after inserting an
  entry but before commit leaves the previously committed prefix recoverable. A process exiting
  after commit but before responding is handled by retrying the same stable identity.
- Dice/tools may execute before a transaction fails. Committed retries never reroll, but this
  design does not guarantee exactly-once external side effects in the pre-commit failure window.
  Future external tools need their own durable idempotency or result-tape design.
- A database lock timeout is an error, not permission to execute outside the transaction.
  Retry with the original identity. Read-only replay and rerender do not execute tools.
- Storage format is `PRAGMA user_version = 1`; journal/state/event schema version is 1 and the
  runtime compatibility version is `0.2.0`. Stored ruleset/runtime versions must match the active
  registry (or an explicitly injected rule implementation). Unknown versions and nonempty unversioned databases are rejected. No automatic
  migration, hash rewrite, or history repair is performed.
- Future migrations must preserve the original artifact, explicitly map old versions, and
  verify replay equivalence before adoption. Replay incompatibility requires a runtime version
  change. The generic-fantasy ruleset keeps its existing `0.1.0` rule semantics.
- The older standalone `JsonlJournal` remains a single-writer scaffold. It now flushes/fsyncs
  before publishing its cache but cannot provide SQLite's atomicity or concurrency guarantees.
  A partial write/fsync failure requires inspection and reopening before retry; there is no
  automatic torn-tail repair. Unversioned JSONL records receive model defaults, but old hashes
  involving unordered sets may fail current verification. There is no automatic JSONL import.

Public JSON schemas are generated from the Python models in `schemas/`. Regenerate them with
`python scripts/export_schemas.py` using the project environment. `JournalPort` now requires
`find_by_idempotency_key`; `MemoryPort`, `DicePort`, and `NarratorPort` are unchanged. Narrators
receive detached committed records and cannot mutate the authoritative repository through them.
