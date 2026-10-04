# Risu single-branch sessions — 2026-10-04

This follow-up to [the identity fixes](risu-identity-context-fix.md) removes the per-turn
branch copy. The bridge forked a new branch for every user action and copied the whole
committed prefix into it, so an ordinary unedited chat produced one branch per turn and
quadratic journal storage. A chat now continues on one branch.

## Resulting contract

- **Append in place:** a new action is appended to the branch whose head is its parent event,
  starting with `main`. Consecutive turns never create branches.
- **Fork only on divergence:** when no branch ends at the parent (a genuine semantic edit of
  an already-continued turn), the bridge forks that committed parent prefix into a branch
  named after the new action. That alternate line then also continues in place.
- **Lookup by chained event id:** each event id includes its parent, so one id identifies its
  whole action prefix. A request locates the committed event in any branch, verifies that
  branch's prefix against the submitted identities, and replays it before interpreting intent
  or rolling. A located event with a different prefix returns 409.
- **Guarded commits:** `SQLiteWorldRepository.dispatch` accepts `expected_seq` and
  `unique_in_world`. Both are checked inside the write transaction and apply only to a new
  commit. A racing request that moves the head, creates the fork, or commits the same action
  first causes a `RepositoryConflict`; the bridge reselects (at most three attempts) rather
  than committing one action on two branches.
- **Reservations:** an empty fork is reserved for the action it is named after, so an
  interrupted fork is reused by its retry and not taken by another edit. A branch reserved by
  another action's unresolved external tool event is never selected; a branch with this
  action's own reservation is preferred.
- **Response `branch_id`:** it now names the branch that committed the action (`main` for an
  unedited chat) instead of always equalling the event id. The PocketRisu provider does not
  read this field.
- **Earlier layout:** worlds written with one copied branch per action remain valid without
  migration. Their events are located the same way, and the next action is appended to the
  latest of those branches.

Storage v2, event/state schema v1, runtime compatibility `0.2.0`, bridge identity v2 hashes,
the legacy v1 read-only rule and the provider plugin are unchanged. `locate_event` and the
`through_seq` argument of `entries` are additive repository reads. See
[the integration contract](../RISUAI_INTEGRATION.md) and
[the persistence contract](../PERSISTENCE.md).

One behavior deliberately changed: an entry appended after a committed action by another
writer is no longer treated as contamination of that action. The committed prefix still
replays, and the next host action forks from its parent instead of building on the foreign
entry.

## Verification

The updated and added bridge tests first failed in **11 cases** against `2a3a556`. After the
change:

| Check | Result |
| --- | --- |
| Full `pytest -q` | 207 passed; one existing Starlette/httpx deprecation warning |
| `node --test tests/risu_provider.test.cjs` | 21 passed |
| `ruff check .` / `ruff format --check .` | Passed |
| `python -m compileall -q src` | Passed |

Added coverage: twelve consecutive turns stay on `main` with twelve stored entries and replay
any earlier turn without dice or intent calls; a semantic edit creates exactly one branch and
both lines keep appending in place with matching full replays; a seeded per-turn layout
replays and continues without a new branch; four concurrent different edits of one turn land
on four separate branches with correct prefixes; a prefix that differs from the submitted
identities returns 409 without dispatch. The concurrency tests were repeated six times.

Storage for an unedited chat, measured by driving the bridge against a temporary database
with repeated `check strength 10` actions:

| Turns | Before (branches / entries / size) | After |
| --- | --- | --- |
| 10 | 11 / 55 / 224 KiB | 1 / 10 / 88 KiB |
| 40 | 41 / 820 / 2,416 KiB | 1 / 40 / 164 KiB |
| 80 | 81 / 3,240 / 9,096 KiB | 1 / 80 / 260 KiB |

No live 6011 host check was run. The deployed `saga-runtime` container was not rebuilt and
still runs the earlier image.

## Remaining limits

Requests still submit and verify the whole action history, and each prefix check reads the
branch through the located event, so per-request work stays linear in chat length. The cap of
256 non-comment user turns, including hidden history, is unchanged. Each divergent edit still
copies its prefix once; shared immutable prefixes were not needed for this fix.

Every journal entry still stores a full post-event state. An interrupted fork whose action is
never retried remains as an empty branch. The world-wide event id check has no dedicated
index and scans the world's entry index.

The trusted solo-play limits from the earlier reports are unchanged: client-controlled message
ids, semantic edits and the known seed can still select alternate outcomes, so this is not
adversarial RuleCheeseBench isolation. No LLM intent, NMOS or remote tool binding was added.
