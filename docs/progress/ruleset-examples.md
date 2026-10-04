# Licensed ruleset examples

Date: 2026-10-04 (Asia/Seoul).
Status: requested example implementation complete; full rulebook coverage and native RisuAI
module import remain out of scope.

## Changed

- Added two selectable, system-level examples: `srd-5.2.1@0.1.0` and
  `fate-condensed@0.1.0`. Each uses the existing runtime, dice port, journal, replay, and branches.
- SRD implements ability checks, saving throws, configured weapon attacks, advantage/disadvantage
  cancellation, attack-only natural 1/20 behavior, critical damage dice, and HP reduction.
  Actor stats supply modifiers, proficiency, weapon data, and AC; event payloads cannot override them.
- Fate implements unmodified-roll overcome and creation of new situation aspects against static
  opposition. It records four outcome grades, new-aspect free invokes, and separate boosts.
  Failure/cost choices requiring the GM remain explicitly pending rather than mutating unknown facts.
- Added closed payload models and pre-tool input/state validation to both new modules. Unsupported
  actions fail rather than silently falling back to another system. The legacy scaffold is preserved.
- Added ruleset manifests, source/license/attribution metadata, shared bot description, module-specific
  narrator instructions, example worlds/events, and deterministic pack export/check tooling.
- Added `GET /rulesets`, `GET /rulesets/{id}`, `POST /worlds/{id}`, and
  `GET /worlds/{id}/ruleset`. The repository resolves each world's persisted id/version. Existing
  implicit demo creation remains generic-fantasy; explicit creation chooses a new system example.
- Added optional `RuleResult.resolution` and its API/schema representation. Existing state hashes,
  state/event versions, runtime compatibility `0.2.0`, and SQLite schema version 1 are unchanged.
  Previously stored results without the optional field remain readable. Each new ruleset has its own
  implementation version `0.1.0`; changed module semantics require a new module version.
- Kept explicit injected rule implementations available for tests/hosts, restricted to their exact
  name/version. Worlds cannot switch system through chat content, an event payload, or a duplicate
  create request. Branches inherit the same world's selection.

## Source and licensing evidence

- SRD 5.2.1: official landing page and versioned PDF legal page confirm CC BY 4.0. Implemented
  sections and exact required attribution are identified in `docs/RULESET_LICENSES.md`.
- Fate Condensed: official licensing guidance confirms CC BY 3.0 and directs creators to the
  downloadable SRD. The official ZIP's HTML was read and its exact attribution retained; source
  checksums are recorded. This implementation did not substitute website presentation text for that SRD.
- Generated manifests/prompts retain attribution, license/source links, and modification notices.
  No commercial rulebook, source artwork, logo, or font is bundled. These source licenses do not
  relicense the entire repository or grant rights to other material associated with a game brand.

## Verified

Target: uncommitted working tree on `feat/phase-1-persistent-runtime`, Python 3.12.3.

- `.venv/bin/pytest -q`: **67 passed**, no skips. One existing upstream Starlette/httpx
  TestClient deprecation warning remains. The original 39 tests still pass; 28 tests were added.
- `.venv/bin/ruff check .`: exit 0.
- `.venv/bin/ruff format --check .`: exit 0, 63 files formatted.
- `.venv/bin/python -m compileall -q src`: exit 0.
- `.venv/bin/python scripts/export_schemas.py`: generated current public JSON schemas.
- `.venv/bin/python scripts/export_rulesets.py --check`: exit 0 after generation.

Behavioral coverage includes boundary dice outcomes, SRD attack/check distinctions, proficiency
from state, critical dice without doubled modifiers, invalid weapon validation before an attack
roll, Fate's four grades and advantage tie/failure behavior, duplicate-aspect rejection, and no
tools/state/journal changes on invalid input. Catalog/creation API tests first failed with missing
routes, then passed after implementation.

Integration tests create different-system campaigns in one database, reopen the app, retry events,
replay, and fork. Unsupported stored versions fail closed. The exported files themselves are read
as consumer inputs, executed through SQLiteWorldRepository, reopened with a DicePort that raises
if called, and rerendered ten times with FallbackNarrator. State/history/rolls remain unchanged.

## Not verified and remaining limits

- These examples are subsets, not full playable implementations of all mechanics in the source
  games. Each manifest lists supported and missing mechanics. Fate invocation spending, boost
  expiry, and cost resolution are not implemented; generic HP is unused by the Fate module.
- RisuAI-native `.risum` or card export and the Custom API/intent bridge are not implemented.
  Packs provide a common description, module prompts, metadata, and runtime fixtures. Pasting
  instructions alone does not connect RisuAI to this backend.
- Live NMOS/MCP/model calls, production deployment, and Docker builds were not exercised.
- Source-license evidence applies to the identified content and included notices; it is not an
  endorsement or a general license to a commercial game's other content.
- The existing trusted debug command boundary remains. Fictional legality, declared difficulty,
  and circumstances still require GM/host adjudication; no authorization logic was changed.
- The existing pre-commit external-tool failure window still applies. A committed retry never
  rerolls; a tool may have executed before an unsuccessful persistence transaction.

## Architecture self-review

Rule handlers and validation perform no I/O, clock reads, or unseeded randomness. The registry
uses explicitly registered Python implementations and does not load code from prompts or remote
files. New modules route all dice through DicePort. The adapter selects a versioned handler and
does not contain game mechanics. Exact HP/aspect/boost facts remain in state and committed records.
No LLM mutation path, NMOS internals, new external user-data service, dependency, DB schema change,
secret, or environment-file edit was introduced. No commits were created. Prior untracked work
was preserved and backed up for comparison at `/tmp/risu-trpg-before-rulesets` (temporary only).

## Next recommended step

Use `packs/rulesets/README.md` to inspect/run the examples. For actual RisuAI use, verify the current
Custom API and module-import formats, then connect the persisted selection and committed results
to that outer adapter. Expand a selected system's declared coverage through versioned rule changes
and invariant tests; do not imply missing rules already execute because their source SRD is linked.
