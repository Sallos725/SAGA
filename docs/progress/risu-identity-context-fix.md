# Risu identity and context fixes — 2026-10-04

This follow-up to [the original command demo](risu-command-demo.md) fixes two independently
reproduced review findings before adding LLM integration: cosmetic edits to one command could
reroll, and hiding earlier prompt context could silently start a new genesis branch.

## Resulting contract

- **Identity v2:** a pure synchronous parser canonicalizes validated command kind, actor and
  payload, including case, whitespace, DC zero padding and default fields. The same message
  id and ancestry reuse one event for equivalent commands. Regeneration never calls the
  injected async intent port or dice. New proposals must match the canonical command.
- **Context visibility:** plugin 0.2.0 sends all non-comment user turns with `context_hidden`
  metadata. Disabled and `allBefore` turns keep their committed ancestry. Hidden unknown
  history/drafts return 409 without execution; a hidden latest action is rejected before writes.
  Genuine semantic edits still fork, and new message ids intentionally remain new actions.
- **Legacy compatibility:** v2 uses distinct world/event namespaces, but looks up the old v1
  world first. If it exists, only exact original raw-text prefixes can replay, through either
  request version. Original ids/state/rolls are preserved. New actions, unmatched cosmetic
  edits and unknown truncated prefixes return 409; they cannot create a replacement v2 world.
  Continue new play in a new host chat. V1 cannot create worlds or events.
- **CI:** the provider suite now runs in its own job using Node.js 22 and
  [the official setup-node action](https://github.com/actions/setup-node), alongside Python CI.

No domain/runtime/database schema or authentication changes, state metadata flags, new
dependencies or migrations were introduced. See
[the current integration contract](../RISUAI_INTEGRATION.md) and
[upgrade instructions](../../packs/risu-starter/README.md).

## Verification

The added regression group first failed in **8 cases** against `0ee7446`. After the patch:

| Check | Result |
| --- | --- |
| Focused API suite | 25 passed |
| Full `pytest -q` | 174 passed; one existing Starlette/httpx deprecation warning |
| `node --test tests/risu_provider.test.cjs` | 14 passed |
| `ruff check .` / `ruff format --check .` | Passed |
| `python -m compileall -q src` | Passed |
| `git diff --check` | Passed |

Regression coverage includes equivalent first submissions racing concurrently, cosmetic
changes after restart, a throwing intent port that is never called for regeneration, conflicting
valid interpreter proposals, malformed earlier commands, hidden latest/unknown turns with no
world writes, true first-turn edits, new-message actions, and canonical/noncanonical legacy text.

The actual provider source was also exercised in a Node host harness whose emitted requests
were submitted to the real FastAPI/SQLite adapter. After six committed attacks, `allBefore`
kept all six user actions; a seventh check continued at **seq 7**, with opponent HP preserved
at **2**. Cosmetic command changes reused the entire saved result with zero additional dice;
a hidden never-executed draft returned 409 and left the database dump unchanged. An independent
supervisor probe with a different seed reproduced seq 6 → 7 with HP **1** unchanged, including
a cosmetic edit to a hidden ancestor. These are harness/API checks, not browser observations.

A **copy** of the actual 6011 database was checked against the fully hydrated host snapshot.
Three distinct exact legacy histories replayed through both protocol versions over two repository
opens (**12 successful reads**), retaining original ids, state and rolls while an intent/dice
implementation that raises on use was installed. The copied database dump stayed unchanged;
the original backup SHA-256 stayed unchanged. Unknown placeholder/cosmetic prefixes rejected.
Independent supervisor verification also preserved the original worlds, branches, entries and
snapshots. This demonstrates exact legacy reading, not reconstruction of absent raw metadata.

### Live PocketRisu 6011 verification

Implementation commit `dc4efcbce32f58bb1b06b1cc92557c524b947ce5` was built from a clean
export and deployed as image
`sha256:1f37470cc68b89de2d8df82c6ba39f9f57293d3930e86acf036836edca604a91`.
All **41 Python source files** in the image matched the implementation commit. Plugin 0.2.0
was installed through the host's official import/update path with its existing arguments
preserved; its source hash remained
`692033aa46f767749fc07854b020bdf0f1b7803164c46ea5010382f973507ebd`.
The owned starter card greeting and version were updated to 0.2.0.

- Actual send, user-message edits to `Attack opponent` and `attack  opponent`, and regeneration
  buttons retained the same event, state, rolls and journal. The attack die stayed **18**;
  the damage die stayed **5**, for **8** damage after the +3 modifier.
- The actual assistant menu/scissors control applied `allBefore`. After six attacks at
  **seq 6 / opponent HP 0**, the seventh check continued at **seq 7 / HP 0**. The original
  seq 6 journal remained unchanged.
- Runtime restart, host reload and another actual UI regeneration preserved the seq 7 event,
  state, rolls and journal. The hidden-context screenshot was visually inspected.
- Six live exact legacy reads (three histories through each protocol version) returned 200;
  two unknown placeholder prefixes returned 409. The original two legacy worlds, six entries,
  all branches and snapshots matched the pre-upgrade backup.
- All original host messages in three chats (0/2/5 messages) and the plugin arguments were
  preserved exactly. The live test chat was retained, and a new blank **New Chat 5** was
  selected for the user.

These live checks demonstrate stored-result/history preservation. Zero additional dice and
intent calls are established by the instrumented API tests above, not by live process counters.

Local evidence is in `/tmp/saga-risu-identity-fix-evidence/`: `regression-red.txt`,
`worker-pytest.txt`, `worker-node.txt`, `worker-compatibility-results.json`,
`worker-verify.py`, `worker-verify.cjs`, `supervisor-probe.json`, `ui-cosmetic.json`,
`ui-hide.json`, `ui-restart.json`, `live-preservation.json`, `host-preservation-ready.json`,
`6011-v2-hidden-context.png`
and the worker contract ledger. Frozen-source hashes identify what was tested and deployed.

## Remaining limits

V1 worlds are deliberately read-only; arbitrary old cosmetic edits cannot be matched without
original raw metadata. Restore exact old history for reading, or use a new host chat for v2.
Upgrade plugin/backend together and stop old writers; mixed old/new servers sharing a database
are not a supported rolling-upgrade configuration.

This remains a trusted solo-play demo. Client-controlled message ids, true semantic edits and
the known server seed can still be used to select alternate outcomes. The fixes do not establish
adversarial RuleCheeseBench isolation. No LLM, NMOS or remote tool binding was added.

The earlier lower-priority limitations remain: a maximum of 256 non-comment user turns
including hidden history, quadratic copied-prefix storage, recoverable empty branches after
rule-validation failures, fixed demo ruleset, and host-specific compose addresses.
