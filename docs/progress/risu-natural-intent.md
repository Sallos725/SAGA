# Risu natural-language intent slice

Implemented on `codex/p1-natural-intent` from `eb8ca8d`. Plugin/card/manifest 0.4.0 use
completion protocol 3. Runtime semantics `0.2.0`, game schema 1 and storage schema 2 are
unchanged. The implementation phase below was verified offline without deployment or live
calls. Subsequently, the backend and plugin 0.4.0 were deployed to 6011 with separate user
approval: `상대를 공격한다` and one regeneration passed with unchanged state/rolls/journal/tape.
See the [bounded live verification](risu-natural-intent-live.md). The existing host card was
retained; the new 0.4.0 starter card was not imported. Other natural-language cases remain
live-unverified.

## Result and scope

The existing one-field Vertex setup now permits a single natural-language SRD attack,
ability check or saving throw. Examples: `상대를 공격한다`, `힘으로 난이도 10 판정을 한다`,
`Make a dexterity saving throw, DC 12`. The fixed actor is `player`; the only target aliases
are `opponent`, `상대`, `상대방`, `적`, and the target must exist in the committed roster.
No actor is invented from card/lore text; a mention of an unknown goblin is not mapped to
`opponent`. Ability aliases are the six English names and 힘/민첩/건강·체질/지능/지혜/매력.
A check/save requires one explicitly written `DC` or `난이도` integer in 1–100.
The entire token is checked: decimal, scientific, fractional and letter-mixed forms are
refused rather than truncated. Sentence punctuation (`DC 10.`) and ordinary Korean particles
(`난이도 10으로`) are accepted. Advantage
and disadvantage require their English word or 유리/불리 explicitly in the action.

Deterministic commands still bypass model interpretation and retain key-free fallback.
The leading words `attack`, `check`, `save` reserve the strict existing command syntax;
malformed commands are not sent to a model for paid repair. Use a full sentence such as
`I attack the opponent with my sword` for English natural language. Blank actions and the
host's synthetic `*says nothing*` placeholder reject before any world or tape write.

The adapter requests a structured JSON proposal, then validates a strict discriminated union
and the existing SRD payload model locally. Proficiency, actor selection, event ids, rulesets,
rolls, modifiers, damage and state fields are not model-selected inputs. Exact target/ability
mentions and numeric difficulty are checked against action text, preventing missing defaults.
The model is instructed to refuse ambiguous, negated, hypothetical, unsupported or multiple
actions. These semantic decisions still depend on model quality; mocked tests verify the
refusal path and structural protections, not actual Gemini language understanding.

Malformed or refused output becomes the bounded tape value `{"kind":"refuse"}` and a generic
422 response, with no game event or dice. It does not persist provider text or provider error
messages. Since the durable tape needs an existing branch, a refused first natural action can
leave an initialized world at sequence zero and a completed refusal record. It changes no HP,
rolls or journal entries. Clarifying the text gives the action a new identity.

## Identity, persistence and execution

The bridge already skipped interpretation/dice for committed actions before this change.
That existing behavior remains the first path, including replay without credentials. This
change adds only the natural-language boundary and durable **precommit** interpretation.

- Protocol 3 retains the `saga-risu-v2` world namespace. Canonical commands retain their exact
  `saga-risu-action-v2` hashes, so upgrading a v2 chat does not reset it.
- Natural text uses `saga-risu-action-v3` with world, parent, host message id and a tagged
  `{kind: natural_language, schema_version: 1, actor_id, text}` payload. Raw text is not
  normalized: even a whitespace edit intentionally creates a different natural action.
  Hash calculation never calls an LLM. A mixed history reports `identity_version: 3`.
- Legacy v1 worlds remain exact-history read-only under protocol 1, 2 or 3. Hidden natural
  turns must already have a matching committed prefix. Drafts and edits return 409 before
  interpretation; no hidden action is executed automatically.
- The existing `SQLiteToolResultTape` is reused. No interpretation cache, table, migration,
  provider-id mapping, or alternate persistence system was added.
- The tape identity is `(world, main, host-event-id, risu-natural-intent-v1)`, independent of
  the branch eventually selected for dispatch. Immutable arguments contain action text,
  fixed actor and roster aliases; `context_hash` binds the verified parent's committed state.
  A first action uses persisted genesis via the readonly `get_initial_state` helper, even
  when another branch has advanced or the stored genesis differs from the registry example.
- A durable pending claim commits before calling Vertex. The model call happens outside DB
  transactions. A validated proposal/refusal is saved before dispatch, and concurrent
  identical requests cannot interpret twice. The proposal stays `completed` on the generic
  tape; the game journal separately records whether its event committed.
- A crash after interpretation but before dispatch reuses that proposal on restart. A racing
  branch change only reselects the existing repository append/fork path; it cannot create a
  second interpretation identity. `expected_seq`, world-wide event uniqueness and exact
  prefix checks remain intact.
- A timeout, OAuth/HTTP failure or uncertain execution returns generic 503 and leaves the
  claim pending. No automatic retry, expiration or blind reset is implemented. The provider
  may finish after local cancellation; a saved result can be reused, while an unresolved
  pending claim requires future reconciliation. Changing credentials never bypasses a claim.

Raw natural action text **is retained** in the local tape request. The tape also retains its
state hash and safe proposal/refusal. Credentials, tokens, narration context, provider error
text and rejected model output are not stored there. Existing tape/history retention is
unchanged. Protect the save volume as campaign data.

## Provider and timing contract

Intent reuses the existing request-scoped Vertex credential validation, fixed OAuth URL,
project/global endpoint and `gemini-3.8-flash` transport. No authentication policy, secret
setting, host core/API/image, endpoint override or new dependency was added. Intent gets
only the action and known target aliases; changing regenerated prose cannot invalidate a
saved interpretation. Narration remains a separate postcommit call with bounded host context.

Structured output uses `responseMimeType: application/json` and `responseSchema` with `anyOf`,
properties, required fields and string enums, as documented in Google's
[structured-output guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/control-generated-output).
Local strict validation is mandatory because unsupported schema fields may be ignored by
the provider. Intent output is capped at 1,024 tokens; narration remains 2,048. Each generation
has a 25-second outer budget. The tape/async bridge has a 27-second wait; plugin proxy/wait
budgets are 65/67 seconds to accommodate both stages and storage/transport overhead.

Maximum generation calls per request with Vertex configured:

| Request | Intent | Narration |
| --- | --- | --- |
| New valid natural action | 1 | 1 |
| Replay of committed action | 0 | 1 |
| New deterministic command | 0 | 1 |
| Reuse of saved precommit proposal | 0 | 1 after commit |
| Refused natural action | At most 1 | 0 |
| Unresolved pending interpretation | 0 additional | 0 |

Without Vertex configuration, commands and committed replays use fallback with zero provider
calls. No generation retries are added. Existing OAuth refresh behavior is unchanged. The
owner requires explicit approval of call count, estimated cost and uncertainty before agent-run
paid tests; the earlier two-call narration approval was consumed and is not reused here.

## Existing references reused

This design follows existing turn identity/idempotency patterns; regeneration protection is
not a newly invented store. The reviewed sources below were inspected, not executed as part
of this change.

- **Aethrion** [`708f60739f13b5cbac9b17de13d3a6873101c2d8`](https://github.com/simulacre7/aethrion/tree/708f60739f13b5cbac9b17de13d3a6873101c2d8):
  `lib/aethrion/bridge.ex` `reader/2` (1034–1049) keys readings by interpreter, previous
  checkpoint, target and line; `bridge/store.ex` preserves the first write via ETS
  `insert_new` and optional JSONL persistence. `test/aethrion/bridge_review_test.exs:380`
  covers identical line/history reuse and changed-history reinterpretation;
  `bridge_second_review_test.exs:35` covers restoration after restart. SAGA reuses these
  acceptance scenarios. It retains its own claim-before-call and save-before-event-commit
  tape ordering rather than importing staged-after-reply cache timing or rule reexecution.
- **oneuldo Patina main** [`be2436ac5fe3ced4aae6e402b0ec7d6cac5d4e35`](https://github.com/Sallos725/oneuldo/tree/be2436ac5fe3ced4aae6e402b0ec7d6cac5d4e35):
  `card/lua/patina_backbone.lua` explains next-real-input commit and swipe fingerprints
  (3–21), `processed_ids` deduplication (397–452), and message-bound resource reservations
  that avoid charging on reroll (602–664). Its 100 processed-id/10 swipe-candidate bounds
  and host Lua lifecycle differ from durable SAGA journals. SAGA adopts the stable-turn
  reasoning, retaining its existing commit-before-narration policy. No Lua tests were run.
- **oneuldo memory pilot** `b6eb2ff90b6cff2110f77925fde2c5504b4c0086` remains the host-id
  reference. Its assistant-inclusive full-snapshot revision dedupe/expectedVersion belongs
  to memory synchronization, not SAGA action identity. See the prior
  [host reference audit](../RISUAI_INTEGRATION.md#host-identity-reference-oneuldo-memory-pilot).

## Verification and remaining work

Worker verification on this change: **255 Python tests**, **22 shipped plugin Node tests**,
`ruff check .`, `ruff format --check .`, `python -m compileall src` and `git diff --check`
passed. Before implementation, the new focused tests produced 20 failures and 4 existing-path
passes; the persisted-genesis regression also failed before the readonly helper was added.
The supervising review found integer-prefix acceptance for malformed DC tokens; closure 1
added nine cases (seven failed before the fix, all nine pass after whole-token validation).
Focused tests cover Korean/English attack/check/save, closed/malformed/refused proposals,
explicit grounding, no-credential behavior, command/legacy compatibility, hidden history,
raw text edits, same-text new turns, restart, concurrent claims, precommit failure, immutable
parent snapshots and unresolved pending no-rerun. OAuth/Vertex were mocked with synthetic
credentials. The pre-existing Starlette/httpx deprecation warning remains.

Independent supervising-agent verification also passed on an immutable executable snapshot:
the shipped plugin called actual FastAPI/SQLite and actual Google SDK/Vertex intent/narrator
adapters, with synthetic in-memory RSA credentials, mocked HTTP and external sockets blocked.
Across 18 host requests, the harness checked a new natural attack, ten regenerations with
11 distinct prose outputs and zero extra intent/dice, restart reuse, command bypass, hidden
natural ancestry, a natural check, an edited natural save fork, a refused extra-state proposal
and refusal reuse, and narration-503 fallback. Persisted state, journal, tape and replay were
reread; no synthetic key/token/error markers were present in SQLite. There were four mocked
intent and sixteen mocked narration generations, and zero live calls. The external task
evidence is `supervisor-integration.json`; it is distinct from the unit-test evidence.
During that offline implementation phase, no real credentials were read or exported and no
paid calls, deployment, commit, push or PR was performed. The later separately approved
[6011 deployment and live test](risu-natural-intent-live.md) verified only one natural attack
and one regeneration through the actual host UI; checks/saves and broader Gemini semantic
quality remain unverified live. NMOS, arbitrary card/lore targets, streaming, complete SRD
rules, reconciliation and histories beyond the existing 256-action limit remain later work.
