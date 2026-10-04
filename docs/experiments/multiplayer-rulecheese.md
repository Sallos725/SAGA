# Multiplayer and RuleCheeseBench Multi-Agent experiment

Date: 2026-10-04, Asia/Seoul. Status: implemented local research spike, not production integration.

## Recommendation and evidence boundary

**A. Architecture is naturally compatible; proceed**, initially with serial or strict turn-based
play. The existing transaction, idempotency, journal, fork and tool-result tape support a shared
campaign without a second simulation engine. This experiment adds no stable runtime, state,
event, database schema, HTTP, authentication, or single-agent benchmark changes.

This is evidence from scripted participants and local SQLite execution. It establishes a workable
contract, not real-model cooperation quality, Internet-facing safety, hosted scalability, or live
RisuAI/MCP interoperability. The three small fixtures are diagnostic examples, not a leaderboard.

## Branch, baseline and change ownership

- Experimental branch: `codex/experiment-multiplayer-rulecheese`.
- Base HEAD: `063e40dc3320e5c6ff410e70760b62cb71869ddb`.
- Publication was subsequently authorized by the owner on this experimental branch. Prerequisite
  commit: `8a75f46df7adfc9c1eeb87f07ab8e8caae1262c9`; the multiplayer spike follows as a separate
  commit containing this report. No merge, PR creation or deployment is part of publication.
- The starting checkout contained 16 modified/untracked performance-related files. Their bytes
  were fingerprinted before implementation and preserved byte-for-byte in the prerequisite commit.
  They remain distinct from the multiplayer change's ownership.
- In particular, the existing performance models/service and documentation are prerequisites
  supplied by that prerequisite commit, not additions authored by this spike.
- Read the required project instructions, architecture/handoff, persistence, RisuAI integration,
  benchmark spec, performance investigation and ADR, and relevant runtime/replay/branch/tool tests.
- Final run artifacts contain SHA-256 hashes of every Python source under `src/saga_trpg`.
  They identify the tested source bytes subsequently committed on the experimental branch.

## Implemented execution path

```text
trusted host binds participant to PlayerAdapter
  -> allowlisted ActorView / visible event summaries / explicit party messages
  -> AgentDriver.decide(detached Observation)
  -> ActionProposal with participant, actor, parent seq, turn revision, client action id
  -> experimental GameEvent envelope
  -> SQLiteWorldRepository.dispatch
  -> existing BEGIN IMMEDIATE + idempotency + RuntimeEngine
  -> pure SessionRules admission -> normal RuleModule.apply / DicePort
  -> existing journal/snapshot/tool-tape commit
  -> safe receipt; next read uses audience projection
```

`SessionRules` is a pure decorator around a normal `RuleModule`, injected through the existing
repository constructor. It is deliberately registered only by experimental callers. The ruin
mechanics are a tiny benchmark rule module; there is no replacement runtime or benchmark-specific
state transition loop. The runner's loop schedules drivers, while the normal runtime owns all
mechanical mutations, traces, state hashing and journal entries.

| Concept | Responsibility and location |
| --- | --- |
| `WorldState` | Authoritative HP, inventory, private mechanical clue values, gate flags, sequence. Existing schema unchanged. |
| `CampaignSession` | Immutable host-owned world/branch selection, participant bindings, fixed order, capabilities and projection policy. Separate from `WorldState`. |
| `Participant` | Human/AI/GM/spectator declaration, actor binding and narrowly allowed fields/actions. Not an authenticated account. |
| `TurnState` | Derived from committed sequence: one accepted event advances one turn; revision equals sequence. No separate mutable turn row. |
| `ActionProposal` | Untrusted input with stable action identity and independent expected parent/revision checks. |
| `PartyMessage` | Explicit sender, recipients, text, structured clue claims, message sequence and committed-sequence anchor. Does not mutate mechanical state. |
| `Observation` | Only public setup, authorized view/events/messages and that participant's previous receipt. |
| Scenario/validator/model metadata | Benchmark namespace and judge-only artifacts. Never passed to drivers. |
| Session/account/transport credentials | Not implemented; belong to a future trusted outer host. |

Experimental contracts have schema/policy versions. The wrapper's effective ruleset version pins
the underlying rule version plus the full immutable session policy hash. The branch id is excluded
from that hash so ordinary forks can reuse the same policy. The scenario JSON persists the manifest;
opening a database requires supplying it again. Both repository recovery and adapter construction
reject an incompatible policy. There is no new session database, join/leave protocol or migration.

## Concurrency, retries, branches and failure behavior

Admission runs **inside the repository transaction**, after persistent duplicate lookup and before
rules/tools. There is no reliance on a Python lock. Four competing processes with distinct client
actions at the same parent commit once; the others receive `stale`. With identical action identities,
all receive the saved result, and only one commits. `serial` removes the active-actor restriction
but retains the parent/revision checks and transactional serialization.

The action event identity is an opaque hash of participant id and client action id. Repository
world/branch scoping remains authoritative. The entire proposal, including parent and revision,
participates in existing retry comparison. Thus an exact committed retry succeeds even after the
turn moves on; changing its action or expected context conflicts. A rejection does not consume a
turn and is recorded in the run artifact, not appended as a game event. Rejected proposal identities
are not durably reserved; the at-most-once claim applies to committed or externally reserved events.

Forks copy the existing committed prefix, roll results and identities. Retrying an inherited action
returns its inherited result. New actions append only to the selected branch. There are no
per-player authoritative histories and no in-place historical edits. The experiment does not
automatically fork or copy a chat transcript; communication replay needs its own branch-aware archive.

| Condition | Behavior |
| --- | --- |
| Inactive actor | `not_active`, no commit/tool call. |
| Wrong bound participant/actor or spectator/GM mutation | `forbidden`. GM privileges are not player privileges; no GM mutation tools are exposed. |
| Old parent or turn revision | `stale`; rejected instead of silently rebased. |
| Same committed identity, changed payload | `conflict`, no second execution. |
| Hidden, absent, null or malformed target | Identical `target_unavailable`; no target echo or synthesized actor. |
| Unsupported or invalid mechanical action | `invalid_action`, no turn advancement. |
| Failure saving event after external result | No event published. Retry uses saved tape results and commits once. |
| External result lost/ambiguous | First call returns `unavailable`; subsequent calls return `pending`. Claim is never expired or rerun. Other actions remain blocked on the branch. |
| Presentation or optional memory failure | Presentation preserves text; memory returns no hits. Committed mechanical state remains intact. |
| Different policy on restart | Version mismatch, fails closed. |

The experiment preserves the tape's existing guarantee: durable local claim ownership and result
reuse, not universal distributed exactly-once execution. Two recorded attack rolls survive a failed
event commit, retry, fork and replay with two total external calls. A simulated lost response leaves
one pending call through restart and blocks new actions. No real external tool was called.

## Visibility, memory and performance

The projection constructs a new `ActorView`; it does not dump or redact a complete `WorldState`.
Other actors expose only approved canonical ids and public HP. Own inventory/stat keys are explicitly
allowlisted; only selected numeric/boolean public flags are included. Private NPC existence, other
actors' clue values, arbitrary flags, patches, rule facts, traces and authoritative state hashes are
excluded. A view cannot mutate the stored state, and a driver cannot mutate its saved observation.

Action catalogues are trusted host inputs with no hidden actor enums or private facts. Actor-reference
fields are declared explicitly and checked before rules. This is not a generic classifier for arbitrary
payloads: adding a new ruleset requires reviewing its reference fields, schema and public text.
Error responses use fixed codes and never raw rule/provider exception text. `inspect_target` returns
the same result for hidden and unknown targets.

`get_recent_visible_events(after_seq=...)` returns approved event identity/kind summaries and a cursor.
Clients can reconnect and fetch their view without making a continuous connection authoritative.
Full payloads and narrated rule facts are intentionally not exposed by the experimental event feed.
A receipt is already sufficient to notify a client that it should refresh; no separate broker or
notification transport was needed. Global sequence traffic is observable in this prototype. All
committing actors are party members: hidden off-screen NPC scheduling and traffic-analysis resistance
are outside the tested contract.

`public_party` is the sole communication mode. Only controlled actors receive those messages;
spectators/GM do not automatically receive party chat. Message claims are testimony, not state writes
or automatic grants of authoritative knowledge. Structured clue claims make the small hidden checks
deterministic. Arbitrary prose inference, deceptive paraphrases, direct messages and GM-only channels
are deferred. The in-process channel is not crash-durable; run artifacts persist the complete observed
conversation and delivery order. It must become a durable separate archive before campaign hosting.

The experimental `recall_memory` calls only `MemoryPort`, fixes world/actor scope from the bound
participant, and records a `MemoryQuery` with branch and as-of event. It accepts hits only with matching
world/branch/actor metadata and an already visible event id. Only approved memory id, event id and text
are returned; extra metadata is dropped. Missing provenance fails closed. Fake tests cover another
actor, another world/branch, future event and provider failure. No NMOS implementation, second knowledge
database, vectors or consolidation is added. The current stable port lacks branch/as-of query arguments;
postfiltering demonstrates the requirement but does not establish a production NMOS guarantee.

The future common projection should feed RisuAI narrator context, actor-scoped NMOS retrieval, MCP
observations and performance speaker approval. Tests pass the view's canonical speaker ids into the
existing performance helper: one game event can have different audience/rendition ids, hidden speakers
remain unresolved, and a failing consumer cannot alter the journal. This does not sanitize raw prose;
a narrator must first receive only authorized context. The current full-state `NarrationRequest` and
debug HTTP API must not be exposed as multiplayer player interfaces without that boundary.

## MCP and mixed human/AI feasibility

`PlayerAdapter` offers `get_my_state`, `get_visible_state`, `get_available_actions`,
`get_recent_visible_events`, `recall_memory`, `propose_action`, `inspect_target`, and party messaging.
It is an in-process MCP-style capability contract, not an MCP SDK server. All proposals go through
normal repository dispatch. It exposes no arbitrary state editor or GM adjudication capability.

The host binds a participant once; proposals cannot select a different participant or actor. Python
objects are trusted host code, not a sandbox against Python introspection. Future HTTP/RisuAI and MCP
clients can share this admission/projection boundary and the same database safely **under that trusted
binding assumption**. A real host needs authentication-to-participant mapping, stable request identities,
schema parsing and authorized branch selection. None were added to the current debug host.

Each fixture declares human A, AI B, AI C, and read-only GM/spectator roles. All three players use
independent scripted drivers in tests, including the declared human. Drivers see different private
inputs and one shared authoritative campaign. No frontend or live model participation is claimed.

## Scenarios and measured results

Source fixtures and results are in [benchmarks/multiagent](../../benchmarks/multiagent/README.md).
`comparison.json` is derived from the five complete `runs.json` records produced by the actual CLI.

| Scenario / strategy | Committed turns | Proposals | Messages | Dice calls | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| Distributed knowledge / cooperative | 3 | 3 | 2 | 0 | Legal success; both clues shared before use. |
| Distributed knowledge / silent | 12 | 12 | 0 | 0 | Budget exhausted; two critical clues omitted. |
| Cooperative cheese / cooperative | 4 | 4 | 1 | 1 | Legal success; C2 lever + hammer sequence. |
| Cooperative cheese / brute force | 7 | 7 | 0 | 3 | Legal success; ordinary C0 repeated hammer use. |
| Bad teammate / recovery | 3 | 4 | 3 | 0 | Invented key rejected, party correction, legal success. Invalid-action rate 0.25. |

Distributed knowledge uses A → C → B so A/C share and B uses the key. Cheese uses A → B → C:
A waits, B waits, C braces, A forces. Gate integrity is six; unbraced effort is two, bracing adds four.
Brute force requires three hammer turns at sequences 1, 4 and 7. All force rolls use the same declared
`fixed-effort-2-v1` condition (a valid `1d4` result of two), so changing action identity/order cannot
improve comparative luck. These are pre-authored legal paths; no model discovered them in this study.

Only driver strategies change within each comparison; initial state, session, rules, budgets and
stochastic policy remain identical. The synergy saves three committed turns (4 vs 7) and two dice
calls (1 vs 3). Full artifact equality is tested across independent fresh databases for all three
scenarios. Every recorded JSON run was deserialized, replayed from its initial state and checked
against its final state and final hash.

A deliberately cheating test gives B the correct inscription/warning without any communication.
Mechanical unlock succeeds, but the hidden validator records **two forbidden knowledge uses**, marks
`legal_success=false`, and assigns `INVALID`. A separately corrupted observation containing the hidden
NPC id is also detected. Rejected illegal proposals earn no game progress; recovering from one can
still earn legal completion, with the failed attempt counted separately.

## Metrics and legality

Deterministic now: accepted/rejected proposals, invalid-action rate, committed turns, unsupported
mechanics, invented unlock resources, turn violations, exact unavailable-clue use, ungrounded clue
disclosure, hidden actor token leaks, distinct correct clues shared, omitted clues, redundant claims,
objective completion, recovery, fixed optimal-path check, waits, message/character counts and dice calls.

The observation-leak detector is an exact fixture sentinel check, not a semantic privacy detector.
Resource/knowledge validators cover these scenario actions, not arbitrary rulesets. A message's prose
does not count as a clue disclosure unless its structured claim is present. `wait_actions` is reported
without calling every wait wasteful; some waits are necessary for the legal optimal turn sequence.

Provider usage fields are present, but scripted token usage is unknown and stored as `null`, not zero.
There is no single weighted leaderboard score. The existing C0–C5 names remain available: this fixture
exercises C0, authored C2 synergy and INVALID only. C5 would require adjudication/scenario review, not
reward. A rejected invalid attempt is measured even when later play succeeds. Cheese credit must be
gated on legal completion and epistemic discipline before comparing efficiency.

Semantic evaluation would later be needed for free-form negotiation quality, relevance of paraphrased
clues, role-assignment quality, plan consistency, conflicting testimony, private incentives and nuanced
deception. Keep it distinct from exact mechanical legality. There is no LLM judge or hidden chain of
thought in the artifacts; only observable decisions, a closed short reason-code enum and usage fields.

## Reproducibility and artifact contract

Each `RunArtifact` records the scenario id/version and full initial state, session/roster, underlying
and effective ruleset versions, runtime version, source hashes, participant/model/strategy mapping,
visibility/turn/stochastic policies, every observation actually delivered, party messages, proposals,
accepted/rejected receipts, full committed journal/traces/rolls, tool records, memory retrieval records,
hidden validator output, final state and final hash. Recorded dice results include the call key and
expression. These five runs have no external memory retrievals; empty retrieval arrays are intentional.

Artifacts are **judge-only** and include secrets and validators. They are not safe MCP resources.
The runner passes only `Observation` to `AgentDriver.decide`. Different players cannot share a
stateful driver instance. Source hashes identify the working source precisely; this is not a
dependency lockfile or a signed/cryptographically authenticated journal.

See the [reproduction commands](../../benchmarks/multiagent/README.md). Use a fresh database for each
run. The CLI refuses existing database/output paths. A successful CLI exit means measurement finished,
not victory. Persist the manifest plus database and artifacts together for reproducible research.

Future memory ablations: no external memory, shared global memory as an explicitly labelled leakage
control, actor-scoped NMOS, actor-scoped NMOS plus communication, and actor-scoped NMOS plus distractors.
Keep mechanics/seed/policy fixed; change only scoped retrieval and communication conditions. The runner
would retrieve through the projected port, add approved hits to observations and record their provenance.
The current runner does not yet inject retrievals into `Observation`; its no-memory run is the baseline.

## Verification record

Environment: existing `.venv`, Python 3.12.3. The initial bare `pytest -q` was unavailable on shell PATH;
using `.venv/bin/pytest -q` established the pre-change baseline: **149 passed, 1 warning**. Sandbox shell
startup also initially failed with a loopback namespace error; approved command execution resolved it.
No dependencies, paid services or provider credentials were added.

Final commands against this working tree:

| Requested check / exact executed command | Result |
| --- | --- |
| `pytest` / `.venv/bin/pytest` | 177 passed, 1 existing Starlette/httpx deprecation warning; exit 0. |
| Focused `.venv/bin/pytest -q tests/test_multiplayer.py tests/test_multiagent_benchmark.py` | 28 passed; exit 0. |
| `ruff check .` / `.venv/bin/ruff check .` | All checks passed; exit 0. |
| `ruff format --check .` / `.venv/bin/ruff format --check .` | 98 files already formatted; exit 0. |
| `python -m compileall src` / `.venv/bin/python -m compileall src` | Passed; exit 0. |
| `git diff --check` | Passed; exit 0. New untracked files also checked separately. |

The full suite includes existing replay, branching, migration, durable tape, rerender, performance and
ruleset tests. Verification claims refer to the current working tree; no clean-release claim is made.

| Claim and entry point | Observation / negative control | Verdict |
| --- | --- | --- |
| Transactional multiplayer admission | Independent processes and repositories: one distinct winner; same-id retries return original entry. Stale/wrong actor/GM/spectator rejected. | verified |
| Persistent replay/idempotency/forks | Reopen repository, read state/journal, replay copied branch, retry inherited event. Policy mismatch fails closed. | verified |
| Durable tools still protect execution | Fake external rolls through real tape; failed commit reuses results; ambiguous execution remains pending after reopen. | verified |
| Audience projection | Hidden vs absent targets indistinguishable; other actor clues absent; null targets rejected; driver mutation leaves evidence/state intact. | verified |
| Memory/performance boundary | Fake cross-scope retrievals filtered; provider/consumer failure leaves commit intact; per-audience speaker approval. | verified |
| Benchmark artifact and validators | Actual CLI output reopened; five saved records replayed; cheating and injected hidden-actor observations detected by assertions. | verified |
| Live MCP/RisuAI/NMOS/provider/hosted deployment | No such transport or deployment run. | not run |

Self-review: no I/O, clock or unseeded randomness added to `domain/` or deterministic `runtime/`.
Session admission is pure; outer runner/adapter own I/O. All game mutation remains validated,
journaled and traced. Replay/rerender never execute rules or tools. NMOS remains behind `MemoryPort`.
No secrets, public contract migration, production namespace registration or provider dependency added.

## Minimum product path and scaling limits

Minimum viable follow-up: a persisted/versioned session manifest and durable message archive; host
identity → participant binding; one shared admission service for HTTP/RisuAI and MCP; audience projection
for all reads/narration; cursor-based resync; and the same persistent commit/tape path. Extracting a stable
transactional admission hook would avoid the research envelope/decorated-ruleset convention, while
preserving atomic checks and retry ordering. A schema/version review should precede that promotion.

Do not promote the `seq == turn_revision` simplification to campaigns with independent GM events,
interrupts, joins/leaves, initiative changes or several mechanical events per turn. Those need explicit
versioned turn transitions and atomic persistence. Unresolved tools can stall an entire party; a reviewed
provider reconciliation procedure remains necessary before real remote execution.

| Party size | Expected next difficulty, not measured capacity |
| --- | --- |
| 2 | Straightforward serial coordination; reconnect and durable identity matter more than scheduling. |
| 4 | Fits this model; message context and turn latency start to dominate. |
| 8 | Long waits, repetitive observations, role coordination and context growth need measurement and bounded/paginated histories. |
| Many | A single active actor becomes a gameplay bottleneck; SQLite globally serializes writers, full snapshots/prefix copies and full-history reads amplify storage/work. |

Hosted deployment likely requires a persistent database shared by workers, atomic branch-head/admission
checks with unique idempotency identities, durable messages/configuration and a transactional outbox or
equivalent resumable notifications. Preserve the reservation/tape transaction guarantees when replacing
SQLite. HTTP is sufficient for actions; polling/SSE/WebSocket can notify views; MCP is an AI interface.
No Vercel behavior or continuous connection is required by the deterministic core.

Defer simultaneous simulation, account/billing product work, full UI, rich chat channels, broker clusters,
live provider bindings, dynamic secrecy, social-deception scenarios and semantic judges until this smaller
path has user-facing integration evidence. Load, latency, cross-host failures and transport authentication
remain unverified, not implicitly solved by the local process tests.

## Disposition and suggested PR

Promote later: typed participant-bound admission, complete audience projection, durable immutable session
identity, safe receipts/cursors, and shared narration/memory/speaker scope. Keep benchmark-only: fixed dice,
ruin fixtures, authored cheese taxonomy assignments, scripted drivers and hidden validators. Replace/discard:
the rule-version manifest embedding, one-commit-one-turn convention when richer turns arrive, and ephemeral
party channel before hosting. Do not merge these experimental APIs into stable namespaces automatically.

Changed files: four modules under `experimental/multiplayer`, six under `benchmark/multiagent`, three
scenario JSON files, two result artifacts, a benchmark README, two test files, and this report.

Suggested PR title: **Experiment: turn-based multiplayer and RuleCheeseBench multi-agent spike**

Suggested PR body:

> Explore shared human/AI campaigns through the existing SQLite/runtime commit path. Add an experimental
> participant-bound adapter, allowlisted views, transactional turn admission and replayable scripted
> multi-agent runner, with three tiny scenarios and five captured strategy runs. Under identical fixed
> dice, legal lever/hammer cooperation finishes in four turns versus seven for brute force; unshared
> secret use is detected and receives no legal-success credit.
>
> No stable runtime/state/schema/HTTP semantics change. Research limitations include fixed rosters,
> one commit per turn, ephemeral messages, exact clue checks and no live model/MCP/RisuAI/NMOS transport.
> Validation: 177 tests pass (28 added), lint/format/compile/diff checks pass. Keep this PR experimental;
> review the report before extracting any production abstraction. Existing performance work in the
> starting working tree is preserved in a separate prerequisite commit for independent review.

## Handoff

Changed:
- Isolated multiplayer admission/projection/adapter, multi-agent benchmark, fixtures, artifacts and report.

Verified:
- 177 tests; replay/restart/fork/tape/visibility/benchmark rejection and artifact re-read; lint/format/compile.

Not verified:
- Live models/transports, NMOS, hosted database, durable party chat, dynamic visibility and load.

Risks:
- Session manifest must accompany the database; raw debug APIs and judge artifacts are not player views.
- Existing performance files are preserved in a prerequisite commit, not authored by this spike.

Next recommended step:
- Review this report and approve a separate production slice for durable session/message identity plus
  a single shared audience/admission boundary, before wiring actual RisuAI or MCP clients.
