# Persistent runtime contract

SAGA uses `SQLiteWorldRepository`, an outer I/O adapter around
the same deterministic `RuntimeEngine`. SQLite is in Python's standard library. No service,
new package dependency, NMOS binding, or external tool client is required.

## Storage and transactions

The debug HTTP host stores worlds at `$TRPG_DATA_DIR/worlds.sqlite3`; the default directory
is `./data`. `TRPG_DEFAULT_SEED` applies when a world is created. Each world stores its seed,
initial state, ruleset name/version, and runtime version; changing the environment seed does
not change an existing world's future rolls. An explicitly injected `DicePort` overrides the
reference dice for that repository instance and must be pure/reproducible. Non-pure external
dice must use `external_dice=ToolBinding(...)`, described below; injecting a raw remote
`DicePort` bypasses the tape and is unsupported.

The default repository resolves each world's stored ruleset id/version through the built-in
registry; different campaigns can use different systems in the same database. Explicitly
injected rule implementations remain restricted to their exact name/version for tests or hosts
that need them. `POST /worlds/{world_id}` selects a registered
system and creates its example world; existing implicit demo creation still uses generic-fantasy.
See [the ruleset examples](../packs/rulesets/README.md) for the public catalog and context routes.

Each dispatch opens a connection, starts `BEGIN IMMEDIATE`, loads the current branch state,
checks event identity, executes rules through the runtime, inserts the journal entry and any
due snapshot, and commits. The adapter returns the result only after commit. SQLite serializes
writes across threads and processes, including the idempotency check and local dice execution.
For taped external dice, a missing result suspends speculative rule evaluation. The transaction
persists an event reservation, then the outer adapter executes through the tape outside that
transaction and evaluates the same rules again using recorded results. Partial rule state is
discarded. Rule implementations must be deterministic and stateless apart from their explicit
input/output state. All current modules satisfy that contract.
The connection uses foreign keys, `synchronous=FULL`, and a five-second lock timeout. Database
initialization is also transactional. Use a local filesystem with SQLite-compatible locking.

Storage v2 contains six tables:

| Table | Stored data |
| --- | --- |
| `worlds` | Initial state, seed, ruleset/runtime compatibility metadata |
| `branches` | Branch id, parent branch, fork event id and sequence |
| `entries` | Committed event/result, trace, roll records, before/after state hashes |
| `snapshots` | World state and hash at a committed sequence |
| `tool_events` | Pending/committed event reservation, proposal, identities, input state hash |
| `tool_executions` | Stable call identity, argument/request hashes, versions, status, result |

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

`get_initial_state(world_id)` reads the persisted genesis through the existing state loader,
without snapshots or a moving head. It performs no rules or dice and changes no schema.
The Risu natural-intent adapter binds its existing generic tape claim to this state for first
actions, or to a verified committed parent for later actions. Its call is anchored to `main`
even when the game action later forks. Completed proposals/refusals remain tape records;
game commitment is represented by the existing journal. Raw natural action text is retained
in tape arguments, while credentials and rejected provider output are excluded. See the
[natural-intent contract](progress/risu-natural-intent.md) for pending/crash behavior.

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

`dispatch` accepts two optional preconditions for hosts that choose a branch before writing.
`expected_seq` requires the branch head to be unchanged, and `unique_in_world` rejects an
event id already committed on another branch of the world. It requires an explicit `event_id`
(or a reservation that already fixed one); a generated id is rejected with `ValueError`. Both are evaluated inside the
write transaction, apply only to a new commit (retries still return their record), and raise
`RepositoryConflict`. `fork` likewise accepts `unless_committed`, which refuses inside the
fork transaction to create a branch for an event id the world already holds. `locate_event` reports, per branch containing an event (or genesis), its
sequence, the branch head, fork sequence and any unresolved tool event. The PocketRisu bridge
uses these to append consecutive turns to one branch and fork only on divergence.
`discard_empty_branch` removes, in one write transaction, a fork that holds nothing beyond its
copied prefix: the branch row, copied entries and snapshot go together. It refuses `main`, a
branch with its own entries, any tool event or tape record, and a branch that has children. The bridge calls it when
the rules reject the edited action a fork was just created for, so failed edits do not
accumulate empty branches; a racing identical request that already committed keeps its branch.

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
the existing private deployment boundary. Tool identity conflicts return 409; unresolved
executions return 503 without dispatching another remote call. Tool configuration is injected
by the host, not selected through untrusted HTTP event payloads.

## Durable external tool-result tape

`ToolResultTapePort` accepts a versioned `ToolCall` and a `ToolPort`. Its SQLite implementation
works with any JSON-valued result; it has no MCP dependency. `RecordedDice` adapts those results
to `RollRecord` for existing rules. A host can configure the repository with:

```python
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.ports.tools import ToolBinding

# remote_tool implements execute(ToolCall, *, idempotency_key) -> JsonValue.
repo = SQLiteWorldRepository(
    "data/worlds.sqlite3",
    external_dice=ToolBinding("dice", "1", "provider-protocol-1", remote_tool),
)
```

No remote implementation is supplied yet. Dice arguments are `roll_key` and `expression`;
the result must validate as a matching `RollRecord`. Before consuming it, `RecordedDice`
checks the requested expression's die count, die ranges, and modifier, and requires
`total == sum(raw) + modifier`. Expressions use the same grammar and bounds as the reference
roller (including `d6`, `2d6+3`, and `4d3-8`). The tool receives a detached call, never a mutable
`WorldState`. Pure local dice continue using the original path.

Identity is `(world_id, branch_id, event_id, tool_call_key)`. Canonical JSON SHA-256 hashes
normalize dictionary order while preserving array order and distinguishing booleans/numbers.
Nonfinite numbers are rejected. A different argument hash, tool name/version, protocol version,
or invocation context under that identity raises `ToolIdentityConflict`. The repository binds
context to the complete action, input state hash, runtime version, and ruleset version.
This also detects changed targets or other action inputs even if dice arguments are identical.

Execution phases:

1. Persist an event reservation, including its generated event id if only an idempotency key
   was supplied. External dispatch requires at least one explicit retry identity.
2. Under `BEGIN IMMEDIATE`, check the tape and insert a unique `pending` claim; commit it
   **before** calling the remote tool. A competing process returns the completed result or
   receives `ToolExecutionPending`. It never steals or expires the claim.
3. Invoke the tool outside the transaction, passing `saga-tool-<canonical request hash>` as
   the remote idempotency key. Adapters should forward it when their remote supports it.
4. Persist the JSON result as `completed` in a separate transaction before returning it.
5. Reevaluate the event from its reserved state using recorded results. Additional missing
   calls repeat phases 2–4. Commit journal, snapshot, and `committed` tape/reservation status
   in the same final world transaction.

An unresolved event reserves its branch: a different event cannot mutate that branch, even
through a host configured for local dice. Reads, committed duplicate requests, replay, and
forking a committed prefix remain available. Identical retries reuse the reservation and
completed results. Changing the pending proposal fails; omitting an optional identity is
allowed only when another supplied identity matches. Forks copy committed journal results,
not pending claims; new branch calls have independent identities.

Rerender reads a detached committed event. Recovery/replay/fork consume recorded states and
rolls without running rule code or tools. Their operation does not require a configured
remote tool or process-local cache.

## Failure and compatibility policy

- SQLite rolls back a failed event/snapshot transaction. A process exiting after inserting an
  entry but before commit leaves the previously committed prefix recoverable. A process exiting
  after commit but before responding is handled by retrying the same stable identity.
- A failure before the tool claim commits cannot execute that tool. A failure after result
  persistence but before event commit retains the result for retry, even across process exit.
  A failure after the pending claim commits but before its result is saved is **ambiguous**:
  the remote may or may not have executed. This includes timeouts, invalid/non-JSON results,
  disk failure while saving a result, and a crash just before sending the request.
- Generic exactly-once remote execution is impossible without remote cooperation. SAGA
  provides durable local claim ownership and result reuse, not a distributed transaction.
  `pending` means in-flight **or** ambiguous; there are no wall-clock leases or automatic
  retries. A provider-specific, audited reconciliation path must be implemented before live
  MCP enablement. This slice deliberately has no reset/delete/retry-pending endpoint.
  A malformed or inconsistent persisted dice response also fails closed on every retry. Its
  tape record remains `completed`, not `committed`; world state and journal remain unchanged.
  Reopening the repository and retrying never reruns that recorded call.
- The tape and event history live in the same SQLite file and must be backed up/restored
  together. Restoring an older backup can lose knowledge of later remote side effects;
  do not resume such operations without reconciling with the remote service.
- A database lock timeout is an error, not permission to execute outside the transaction.
  Retry with the original identity. Read-only replay and rerender do not execute tools.
- Storage format is `PRAGMA user_version = 2`; journal/state/event schema version is 1 and the
  runtime compatibility version is `0.2.0`. Stored ruleset/runtime versions must match the active
  registry (or an explicitly injected rule implementation). Unknown versions and nonempty
  unversioned databases are rejected. Opening v1 requires the explicit migration below.
  No automatic migration, hash rewrite, or history repair is performed.
- The older standalone `JsonlJournal` remains a single-writer scaffold. It now flushes/fsyncs
  before publishing its cache but cannot provide SQLite's atomicity or concurrency guarantees.
  A partial write/fsync failure requires inspection and reopening before retry; there is no
  automatic torn-tail repair. Unversioned JSONL records receive model defaults, but old hashes
  involving unordered sets may fail current verification. There is no automatic JSONL import.

## Migration contract

Stop the host and run, with a new backup path:

```bash
python -m saga_trpg.adapters.storage.migrations data/worlds.sqlite3 \
  --backup data/worlds.pre-v2.sqlite3
```

`migration_path` is a pure ordered version plan. The only supported step is actual storage
v1 → v2: add the event-reservation and tool-execution tables/index. There is no invented old
state format. World/event/journal JSON and runtime/ruleset semantics remain exactly unchanged.
An already-current database returns without a backup or rewrite. Unknown/future versions fail
closed; a backup file is never overwritten.

The migration holds a SQLite writer reservation, uses SQLite's backup API through a separate
read connection to preserve the committed source (including WAL content), checks database
integrity, replays every branch, and validates every snapshot. It applies ordered DDL and
version changes in one transaction, then repeats replay and checks both final state hashes
and the byte content of original stored row values. Any error rolls back DDL/data/version
together. A successful backup remains available even when subsequent validation fails.
Restore it only with the host stopped; retain the failed source for investigation.

Custom rule implementations absent from the built-in registry cannot currently be migrated
by this CLI. A future semantic state migration must define an explicit schema/runtime
transition and its replay contract; it cannot be slipped into this storage-only step.
Legacy JSONL import remains deferred. Tests use a fixture exported by the actual prior v1
implementation, including a committed attack, snapshots, a branch, and fixed expected hashes.

Public JSON schemas are generated from the Python models in `schemas/`. Regenerate them with
`python scripts/export_schemas.py` using the project environment. `JournalPort` now requires
`find_by_idempotency_key`; `MemoryPort`, `DicePort`, and `NarratorPort` are unchanged. New
`ToolCall`/`ToolExecution` models and `ToolPort`/`ToolResultTapePort` describe the tape. Narrators
receive detached committed records and cannot mutate the authoritative repository through them.
