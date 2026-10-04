# PocketRisu command demo — 2026-10-04

## Changed

- Added a closed, versioned `POST /v1/chat/completions` adapter and `/v1/models` discovery.
- Mapped host-instance/chat/user-message identities to persistent worlds and immutable action
  branches using the existing repository. Regeneration looks up the committed entry before
  interpreting a command. Latest-action edits fork from the known committed parent; changed
  earlier uncommitted prefixes reject explicitly.
- Added deterministic SRD attack/check/save interpretation, commit-before-narration ordering,
  deep-copy narrator input and recorded-fact fallback.
- Added a PocketRisu v3 provider with main-only single-use request tickets, stable host identity,
  local-network transport, stale-selection/cancellation guards and authoritative result display.
- Added a v2 starter card, pinned-host compose file, request schema and installation/failure docs.

No `domain/`, `runtime/`, ruleset, memory, tool, database schema or authentication code changed.
Existing HTTP routes remain compatible. Storage v2, event/state schema v1 and runtime semantics
`0.2.0` are unchanged. No credentials, model keys or NMOS internals were added.

## Verified

### Main-based PR checkout

The task was extracted into `codex/risu-6011-command-demo` based on `main`; unrelated
experimental files and tests are excluded from the PR. Full checks from that checkout passed:

- `PYTHONPATH=src python -m pytest -q`: **161 passed**, one existing Starlette/httpx warning.
- `node --test tests/risu_provider.test.cjs`: **11 passed**.
- `ruff check .` and `ruff format --check .`: passed; **95 files** already formatted.
- `python -m compileall -q src` and compose `config --quiet`: passed.

### Initial implementation checkout

The initial verification below ran on the experiment-based implementation checkout, before
the task changes were extracted into a PR branch based on `main`. Its **189 tests** and
**103 formatted files** include experiment-only files; these counts are not results for the
main-based PR. The live-host image recorded below was also built from that initial checkout.

```text
.venv/bin/pytest -q                          189 passed
node --test tests/risu_provider.test.cjs       11 passed
.venv/bin/ruff check .                       passed
.venv/bin/ruff format --check .              103 files already formatted
.venv/bin/python -m compileall src           passed
docker compose -f deploy/compose.risu-6011.yaml config --quiet
                                            passed
git diff --check                            passed
```

The Python run reports the existing Starlette/httpx deprecation warning. It does not fail
verification and no unrelated dependency change was made.
After the full runs, the existing API rejection test and Node server-error test were extended
with the host's synthetic `*says nothing*` input and rerun narrowly. Product source was unchanged.

| Contract | Evidence | Boundary |
| --- | --- | --- |
| `identity-and-idempotency` | Ten API regenerations, restart with zero interpreter/dice calls, same-text/new-id, edit/delete prefix and concurrent duplicate tests | In-process HTTP with real SQLite and instrumented deterministic dice |
| `commit-before-narration` | Narrator observes the persisted event, mutates its copy and raises; saved state survives and retry does not reroll | Injected failing narrator |
| `closed-intent-and-fallback` | Rejected malformed identities, invalid intents, unsupported commands, synthetic `*says nothing*`, mismatched prompt, unsupported stream/purpose, invalid targets | API tests; plugin Node harness covers auxiliary/continue guards; actual default-host placeholder path returns 422 without mutation |
| `plugin-current-host` | Official import, actual user send, ten regeneration buttons, repeated-text/new-message and latest-action edit succeed on PocketRisu 1.13.0 | Real browser → v3 hook/provider → nativeFetch → dedicated runtime → persisted/rendered reply |
| `isolated-deployment` | Dedicated PocketRisu 6011/runtime 4851 health checks, compose validation, host reload and rebuilt-backend restart with actual rerender | Separate containers, network and named volumes; original chat/messages and committed result survive |

Actual browser regeneration retained event
`risu-11a2cad24306bd6d75505ffa58e38e4ea758b7561e61bbea082a5a3860b241e1`,
opponent HP **20/30**, attack die **15**, damage die **7 + 3 = 10**, and the complete stored
journal/state. The host displayed swipe **11/11** and the recorded-result indicator.
This was the real regeneration control, including its confirmation dialog. API instrumentation
separately proves zero additional dice and intent calls; the live UI check proves unchanged
recorded outcomes and history, not hidden instrumentation inside the deployed process.

Live history checks also passed: sending identical text as a new message produced a distinct
event at sequence 2; editing that latest action to `check strength 10` forked from the prior
event at sequence 2 and preserved the prior opponent HP. Host reload fully rehydrated the
same messages. Recreating the runtime from final image
`sha256:c3f0233ffe85fcf0f5a7fe4865b99c0a4cc2644423d781b9ece9d2a771d1d68e`
preserved the result, and an actual rerender still reused it. Live missing-identity and
auxiliary-purpose requests returned 422.

The actual Continue menu exposed a setting-sensitive host path: PocketRisu defaults
`useSayNothing=true`, which inserts a synthetic `*says nothing*` user message before the hook.
Three host POST retries all returned 422 with the journal unchanged. This proves fail-closed
behavior at the interpreter, not zero networking. The dedicated demo must use
`useSayNothing=false` so continuation retains its assistant tail and fails the plugin ticket
guard before networking. On a new chat with that setting, `check strength 10` succeeded,
then the actual Continue menu displayed the explicit unsupported-operation error with
**zero additional backend POSTs** and an unchanged journal. Existing verification chats were
preserved, and a fresh chat was opened for the user.

Local verification artifacts are under `/tmp/saga-risu-6011-evidence/`, including
`worker-pytest.txt`, `worker-node-tests.txt`, `worker-synthetic-api.txt`,
`worker-synthetic-node.txt`, `ui-rerender.json`, `ui-history.json`, `ui-restart.json`,
`ui-continue.json`, `6011-rerender-verified.png`, `acceptance-contract.json` and the worker
contract ledger.
These are local evidence, not files required to run the pack.

## Not verified / limitations

- No live NMOS, remote MCP dice, LLM interpreter/narrator, model streaming, or arbitrary card/lore
  preservation. This demo uses no memory and needs no model credential.
- The main-request replacer is global to the host; disable the plugin when selecting another
  provider to avoid an opaque ticket line in that provider's prompt. Broader provider/card
  compatibility is deferred; only the SAGA provider can consume tickets and dispatch events.
- Continuation and auxiliary requests intentionally fail without dispatch. A new user message
  is required for a new action. Zero-POST continuation requires `useSayNothing=false`; the
  default synthetic placeholder path receives 422 instead. No automatic enemy turn or complete
  combat system is present.
- Active history is capped at 256 user actions. The immutable-prefix strategy copies journal
  prefixes and therefore uses quadratic storage; it is not a long-campaign scale benchmark.
- Old edits must be regenerated starting at the first changed user turn. Silently re-executing
  later actions would violate the safe-retry contract, so unknown changed prefixes return 409.
- Post-commit transport cancellation or selection changes cannot undo the action; the original
  immutable branch retains it. Returning to that same history recovers the result.
- Existing debug API trust assumptions remain. It is bound to localhost and the dedicated
  private Docker network; production multi-tenant authentication is not implemented.

## Self-review

The adapter dispatches only schema-valid `GameEvent` values through the existing repository.
No generated text updates state. Repository commit precedes rendering; replay reads saved
results and never executes dice. External-tool behavior still belongs to the existing durable
tape; this deployment uses local deterministic dice. Exact HP/rolls come from the saved result,
not memory or a status parser. Every committed action retains an event id and ruleset trace.

## Next recommended step

Use the isolated command demo for manual host playtests. Select an explicit LLM provider before
adding optional natural-language interpretation/narration, retaining this command/fallback path.
For longer campaigns, design efficient immutable-prefix storage before raising the history limit.
NMOS can be added independently through `MemoryPort` when its boundary is ready.
