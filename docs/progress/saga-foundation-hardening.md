# SAGA foundation hardening

Date: 2026-10-04 (Asia/Seoul)
Branch: `codex/saga-foundation-hardening`
Base: `2df2d397e09fc4711af3bacb298324716aadcc7b`

## Outcome and scope

Implemented the SAGA identity cleanup, CI, explicit storage migration, and durable external
tool-result tape. At the initial implementation checkpoint, no commit or PR had been created.
The owner's subsequent request authorizes publishing the foundation changes as a PR; unrelated
concurrent changes remain in the working tree. No merge, live database migration, or deployment
is part of this task. Actual NMOS/MCP/RisuAI transports, new game mechanics, and benchmark
expansion remain deferred.

Other work added local dice reference documentation and performance-projection files during
this task. Those changes are preserved and are not claimed as this implementation. In
particular, `docs/LOCAL_DICE_REFERENCE.md`, `docs/PERFORMANCE_LAYER.md`, the performance ADR,
`tests/test_performance.py`, and corresponding edits in shared documentation are separate work.
The foundation ADR uses number 0005 to avoid the independently added performance ADR 0004.

## Files and decisions

| Component | Change |
| --- | --- |
| `src/saga_trpg/`, tests, scripts | Rename package and every existing import; existing test bodies otherwise unchanged |
| `pyproject.toml`, API title, README, Makefile, Dockerfile, Compose, pack metadata | Distribution `saga-trpg`, package `saga_trpg`, project SAGA — Stateful Agentic Game Architecture; Compose service `saga` |
| `SCAFFOLD_NOTES.md`, `TREE.txt` | Rewrite false scaffold assumptions; remove stale generated tree snapshot |
| `.github/workflows/ci.yml` | Full tests, lint, format, compile on Python 3.11/3.12/3.13 for pushes and PRs |
| `adapters/storage/database.py`, `schema.py`, `migrations.py` | Shared SQLite transaction boundary, explicit schema versions, ordered offline v1 → v2 upgrade |
| `domain/tools.py`, `ports/tools.py`, `adapters/storage/tool_tape.py` | Typed, transport-neutral calls/executions and durable SQLite tape |
| `adapters/dice/recorded.py`, repository dispatch | Consume recorded external dice results; reserve event/state across external execution |
| `runtime/engine.py`, `runtime/hashing.py` | Reuse existing proposal comparison and canonical hashing without changing event semantics |
| `api/app.py`, `adapters/dice/mcp.py` | Pending/conflict responses and documented future MCP binding requirements; no live remote client |
| `schemas/tool-*.schema.json`, schema exporter | Publish tool-call and tool-execution contracts |
| `tests/test_migrations.py`, v1 SQL fixture, `tests/test_tool_tape.py` | 31 focused tests in addition to the preserved 67-test suite |
| Architecture, persistence, handoff, task/implementation plans, ADR 0005 | Update supported behavior, upgrade instructions, failure limits, and next work |

No import compatibility shim is provided. Install with `python -m pip install -e '.[dev,api]'`.
NMOS remains one optional `MemoryPort` implementation; all verification ran without NMOS.
Historical reports and the `legacy-scaffold` ruleset manifest enum remain intentional history
or compatibility vocabulary. The starter pack's `status: scaffold` still accurately marks
that unimplemented integration; it is not SAGA's project identity. A concurrently authored
performance report mentions the former import name only to describe the rename in progress.

The workflow follows the published [checkout](https://github.com/actions/checkout) and
[setup-python](https://github.com/actions/setup-python) usage and uses their v7 actions.
No new runtime dependency or distributed service was introduced.

## Migration strategy

Storage v1 is the actual previous SQLite schema. A fixture was exported with that implementation
before modifying storage; it contains an attack, snapshots, and a fork. Storage v2 adds only
`tool_events`, `tool_executions`, and the unique pending-event index. State/event/journal schema
v1, runtime compatibility `0.2.0`, and ruleset semantics remain unchanged.

The host rejects v1 until the operator stops it and explicitly runs:

```bash
python -m saga_trpg.adapters.storage.migrations data/worlds.sqlite3 \
  --backup data/worlds.pre-v2.sqlite3
```

The upgrade reserves the writer, preserves the source with SQLite backup, validates every
branch and snapshot, applies ordered DDL/version changes atomically, and rechecks replay
hashes plus original stored row values. Unknown versions and invalid history fail closed.
Current v2 data is not rewritten and creates no backup. Existing backup files are never
overwritten. SQL failure and post-migration history mutation both roll back the whole upgrade.

The migrated fixture's expected authoritative hash on both branches is
`92279c697d3ab7dd2406363b3d998b15b85c7318bb7cbe2b55e2629b1abc9edc`.
Custom rules absent from the registry and legacy JSONL import remain unsupported by this CLI.
Future semantic migrations need their own explicit schema/runtime transition.

## Tool tape and failure semantics

Call identity is world/branch/event/call key. Canonical argument and request hashes detect
changed arguments, versions, tool names, and context. External dice context includes the
complete game action, reserved input state, runtime, and ruleset version. Tool bindings are
host configuration; a tool receives detached structured input, not mutable world state.

The tape commits `pending` before calling the tool and `completed` before returning its result.
A competing process gets a recorded result or an explicit pending error. Remote idempotency
keys are passed to the adapter. They are useful only if the remote honors them.

The repository evaluates stateless rules until a result is missing, durably reserves that
event, executes outside the world transaction, and reevaluates from unchanged input state.
Final journal, snapshot, reservation status, and **consumed** tape-result statuses commit
together. Other mutations on a reserved branch are rejected; reads and forks of committed
prefixes remain available. Completed results survive a failed final event transaction.

If execution or result persistence fails after a pending claim commits, execution is ambiguous.
No automatic retry, lease expiry, claim deletion, or takeover is offered. Provider-specific
reconciliation must precede live MCP enablement. This is durable local idempotency, not a claim
of generic distributed exactly-once effects. Restoring an old database can lose knowledge of
later remote effects, so restore/reconciliation must account for both sides.

Generic non-dice results are supported by the tape and tested with encounter-like JSON. The
production rule integration in this slice is dice; future consumers must implement their own
typed event orchestration. Bare injected `DicePort` remains for pure/test implementations.
Pending branch blocking, repeated pure rule evaluation, and retaining all records are deliberate
costs of this small design. No automatic cleanup or operator reconciliation UI was added.

## Executed verification

This initial checkpoint refers to the foundation code published as `ae5a9ff` and its
98 collected tests. The PR review follow-up below records subsequent changes. Independent
performance tests are outside this recorded suite. The 51-file foundation verification manifest
(source, existing/new foundation tests, fixture, package metadata, workflow, schema exporter)
has canonical JSON SHA-256:
`d0cbacec17023e3fb8851a2b014e3b99d40bece6ce6c5fb7d4ad8ce6d46d881a`.
It was saved locally as `/tmp/saga-foundation-verification-manifest.json`.

During final checks, the separate performance implementation extended the shared schema
exporter and added 32 tests. The other 50 foundation files still byte-match the successful
matrix target; their canonical manifest hash is
`308315ba933b665bd2159cc5382c6b19bc34ab04ba01574afc6e0a5b4b216324`.
The expanded working tree was then checked on Python 3.12.3: **130 passed, 1 warning in
11.20 s**, Ruff lint passed, Ruff format reported **85 files already formatted**, compileall
and `git diff --check` passed (all exit 0). This is additional compatibility evidence, not
ownership or architectural review of the performance feature. That expanded suite was not
rerun on 3.11/3.13; the recorded 98-test foundation matrix remains the matrix evidence.

| Command / target | Actual result | Exit |
| --- | --- | --- |
| Rename-only baseline: `.venv/bin/pytest -q` | 67 passed, 1 warning, 2.74 s | 0 |
| Final Python 3.12.3: `.venv/bin/pytest` | 98 passed, 1 warning, 5.89 s | 0 |
| Python 3.11.17 clean container: `pytest` | 98 passed, 1 warning, 6.09 s | 0 |
| Python 3.13.16 clean container: `pytest` | 98 passed, 1 warning, 6.24 s | 0 |
| `ruff check .` on all three versions | All checks passed | 0 |
| `ruff format --check .` on all three versions | 79 files already formatted in each final run | 0 |
| `python -m compileall src` on all three versions | Passed | 0 |
| `docker compose --env-file .env.example config --no-env-resolution -q` | Valid; private environment files not resolved | 0 |
| `docker build -t saga-foundation-hardening:test .` | Final foundation source image built | 0 |
| Isolated final-image package/API/restart smoke | Health, event, duplicate, fresh repository replay equality passed; no `.env` or `.git` in image | 0 |
| `python -m pip wheel --no-deps --wheel-dir /tmp/saga-foundation-wheel .` | `saga_trpg-0.0.1-py3-none-any.whl` built | 0 |
| Wheel source inspection | All 37 packaged Python files byte-match source; no former namespace or bytecode cache | 0 |
| Existing test-body comparison against HEAD | All 8 existing test files differ only by import namespace | 0 |
| `git diff --check` | Passed | 0 |

Matrix container builds used a temporary Dockerfile with `FROM python:<version>-slim`,
`COPY . .`, `python -m pip install --no-cache-dir -e '.[dev,api]'`, then the four CI commands.
The temporary harness emitted a Docker warning about a missing default for its Python-version
build argument; both builds supplied that argument explicitly and succeeded. This warning is
not from the repository Dockerfile. The single test warning in each run is the existing
Starlette/httpx TestClient deprecation, also present in the rename-only baseline.

The wheel SHA-256 is
`dc0668f058b8146f51a06d31d8b8ca6eac554513e166f87d0bb794389500cc15`.
The final tested application image is
`sha256:9315fe454a22d8c0ff55cbb0a4e7deab26f3fe12048131e7a7ca7b9cd2ceeea1`.
At the initial checkpoint, GitHub-hosted Actions had not run because the branch was not pushed.
Local Python matrix execution verifies the command set, not GitHub event dispatch or remote
runner configuration. The published PR's checks show subsequent hosted execution results.

### Invariant evidence

| Claim | Entry point and persisted observation | Verdict |
| --- | --- | --- |
| Supported migration preserves state/history | `migrate_storage`, reopen repo, compare original tables, backup version, both branch hashes | verified |
| Migration fails atomically and rejects future formats | Inject invalid SQL and trace rewrite; requery v1/no new tables; CLI version 999 exits nonzero for the expected reason | verified |
| Same logical call executes once | `SQLiteToolResultTape.execute`, reopen/get; reordered args reuse one fake remote call | verified |
| Identity conflict is detected | Changed args/tool/protocol/version/context; boolean vs number; unchanged call counter | verified |
| Process-level concurrency | Six separate executors race; one remote-effect line, one recorded result; fresh process reuses it | verified |
| Ambiguity never triggers redispatch | Process exits after effect; result-save trigger fails; retry returns pending and effect count remains one | verified |
| Event commit failure reuses results | Snapshot failure and process exit after journal insertion; restart consumes two saved rolls and commits one event | verified |
| Rerender/replay never execute tools | Ten renders/replays and duplicate dispatch, reopen without adapter, forked committed prefix; unchanged state/journal/counter | verified |
| Pending action and branch isolation | Changed target conflicts despite identical dice arguments; local-dice bypass/other action rejected; fork remains usable | verified |
| HTTP failure surface | Inject pending remote execution; actual FastAPI requests return 503/409 and persisted sequence remains zero | verified |
| Real remote exactly-once behavior | No provider/client selected or executed | not run |

## Self-review

Inspected the changed diff and new source/contracts, including final status handling.

1. **Can rerender execute tools?** No supported rerender path dispatches rules; it reads detached
   committed records. The ten-render test verifies unchanged rolls/state/history/call counts.
2. **Can repeated external requests produce a second authoritative result?** Not through the
   tape: durable unique claims never auto-reexecute, and completed payloads are immutable.
3. **Can identities be reused for different arguments undetected?** No: canonical request and
   argument hashes plus event proposal comparison reject them, including metadata changes.
4. **Can narration/NMOS mutate authoritative state?** No new access is granted. Existing detached
   narrator records and optional `MemoryPort` are preserved; no NMOS transport was added.
5. **Does restart/replay depend on wall time or process memory?** No: persisted reservations,
   results, snapshots, journal, and stable hashes drive recovery. Separate-process tests cover it.
6. **Can migration silently reinterpret events?** The v1 → v2 step does not touch their data;
   replay and original-value checks reject attempted rewrites, with atomic rollback.
7. **Did rename make NMOS mandatory?** No. Distribution, API, Docker, and examples operate
   without it; `NullMemory` and other memory implementations remain possible.
8. **Does CI include determinism/replay tests?** Yes: unfiltered `pytest` runs the entire suite
   on each Python version. The same matrix commands passed locally; hosted execution is pending.
9. **Are stale names/docs left?** Current project/package names and commands are updated.
   Historical terms and the preserved legacy manifest enum are intentional, as explained above.
10. **Did this add later-phase complexity?** No provider binding, memory store, frontend,
    presentation system, new rules, or benchmark runner belongs to this increment. Independent
    concurrent files are explicitly excluded from this claim.

Self-review found and fixed one issue: marking every tape record with a matching event id as
committed could mislabel an unused generic result. A new test first failed at the status
assertion (`committed` vs expected `completed`, exit 1). The final implementation marks only
keys consumed by that rule evaluation; the test and complete matrix then passed.

## Handoff

Changed:
- SAGA names, Python CI, storage migration, generic durable tape, external-dice wiring, docs.

Verified:
- 98 tests plus lint/format/compile on Python 3.11, 3.12, 3.13; Docker and wheel paths above.
- Expanded concurrent working tree: 130 tests plus lint/format/compile on Python 3.12.

Not verified:
- GitHub-hosted workflow dispatch, live MCP/NMOS/RisuAI, provider reconciliation, other
  operating systems, and the design/live integration of separately added performance work.

Risks:
- Ambiguous execution blocks its event/branch until a future explicit reconciliation path.
- Existing v1 users must stop the host and migrate with a new backup filename.
- Remote effects cannot be undone by SQLite rollback or restoring an older database.

Next recommended step:
- Audit the current NMOS public/local API for a minimal optional `MemoryPort` binding with
  world/actor scope, event provenance, duplicate-write behavior, and fail-soft retrieval.
- Before live MCP execution, separately implement provider-specific ambiguous-call reconciliation.

## Suggested commit and PR

Commit message:

```text
feat: harden SAGA foundation with migrations and durable tool results
```

PR title:

```text
Harden SAGA foundation: rename, CI, migrations, and durable tool tape
```

PR body:

```markdown
External tools can finish before a world transaction fails. Add a SQLite result tape that
persists claims before execution and results before event commit, reuses completed results,
and rejects ambiguous automatic retries. Reserve the event's branch while it is unresolved.

Complete the SAGA package/command rename, add Python 3.11–3.13 CI, and provide an explicit
backed-up storage v1 → v2 migration that preserves original history and replay hashes.
Document the contracts and publish tool-call/execution schemas. NMOS remains optional.

Validation: 98 tests passed on each of Python 3.11, 3.12, and 3.13; Ruff lint/format and
compileall passed on all three. Docker configuration/build/API-restart smoke and wheel
contents passed. The existing TestClient deprecation warning remains.

Upgrade: stop the host and run the documented migration with a new backup path before
opening existing v1 storage. Generic remote exactly-once execution is not promised;
provider-specific reconciliation and live NMOS/MCP/RisuAI clients remain deferred.
```

## PR review follow-up — 2026-10-04

The [inline review](https://github.com/Sallos725/SAGA/pull/1#discussion_r4176044565)
and [repository-owner review](https://github.com/Sallos725/SAGA/pull/1#issuecomment-5976518718)
identified the same valid P1: strict `RollRecord` field validation did not enforce numerical
consistency. An external result with `raw=[1]` and `total=999` reached hit/damage calculations
and committed an invalid world state.

`RecordedDice` now checks die count, die ranges, modifier, and total against the requested
expression before consuming a result. It shares the reference roller's unchanged parser and
bounds. Seed hashing and local roll generation are unchanged. Invalid recorded responses stay
`completed`; dispatch fails without a world/journal commit or another external execution on
retry, including after reopening the repository. Existing committed history is not rewritten.

Verification on Python 3.12.3:

- Before the fix, all 12 new invalid-response cases failed because dispatch accepted them;
  three valid-expression cases passed. Cases cover both attack and later damage results.
- After the fix, `pytest -q tests/test_tool_tape.py tests/test_dice.py`: **40 passed**.
- Isolated staged PR snapshot: `pytest -q`: **113 passed, 1 warning in 5.72 s**;
  `ruff check .`, `ruff format --check .` (77 files), and `python -m compileall -q src` passed.
- Concurrent expanded working tree: **149 passed, 1 warning in 5.67 s**. Its independent
  performance changes remain outside this PR. The warning is the existing TestClient warning.
- The initial PR commit's hosted Python 3.11/3.12/3.13 checks passed. Updated hosted results
  are recorded on the PR; the local follow-up above ran on Python 3.12 only.

Self-review: validation remains at the external dice boundary; deterministic code gains no
I/O, clock reads, or unseeded randomness. No schema changes, dependencies, or provider calls
were added. Live MCP/NMOS/RisuAI and provider reconciliation remain unverified.
