# Optional performance foundation — 2026-10-04

## Decision and scope

Pursue the local primitive now, and defer the complete feature until stable RisuAI action/
rendition mapping and narration are wired. See [the investigation](../PERFORMANCE_LAYER.md)
and [ADR 0004](../adr/0004-optional-performance-projection.md).

Current integration is a callable Python helper after `NarratorPort.render` returns text.
There is no production HTTP/RisuAI consumer, streaming, model inference, TTS, image provider,
presentation archive or database migration in this increment. The prototype uses fake
consumers, not actual external services. No dependencies or deployments were added.

## Changed

New files:

- `src/saga_trpg/presentation/models.py`: closed immutable/versioned models, strict version
  validation, canonical actor/narrator/unresolved references, code point span contracts.
- `src/saga_trpg/presentation/segmentation.py`: pure segmentation of exact text using an
  optional structured sidecar; unknown ids and malformed metadata degrade conservatively;
  stable content-derived event ids and bounded annotation input.
- `src/saga_trpg/presentation/service.py`: disabled-by-default post-text helper, explicit
  delivery statuses, cooperative deadline, fail-open consumer errors, propagated cancellation.
- `src/saga_trpg/ports/performance.py`: optional immutable event consumer and deduplication
  contract. Consumers receive no world/repository/tool/memory object.
- `tests/test_performance.py`: 36 focused cases, including an actual SQLite dispatch followed
  by ten narration passes, failed mutation attempts, restart and replay with counting dice.
- `schemas/dialogue-metadata.schema.json`, `schemas/performance-event.schema.json`.
- `docs/PERFORMANCE_LAYER.md`, `docs/adr/0004-optional-performance-projection.md`, this report.

Updated existing files, preserving their pre-existing changes:

- `scripts/export_schemas.py`: registered the two presentation schemas; retained the package
  rename and concurrently added tool schema exports.
- `docs/ARCHITECTURE.md`: added the optional performance boundary and report links.
- `docs/progress/README.md`: linked this separate investigation.

Existing package rename, storage/tool-tape/migration work, API changes, CI files and other
working-tree changes belong to prior/concurrent work, not this increment.

## Verification

Commands used the existing `.venv` under Python 3.12. System Python lacked pytest; no install
was necessary. Local command execution needed an approved sandbox fallback because bubblewrap
failed to initialize its loopback interface. No provider/network behavior was tested.

| Command / gate | Exact result |
| --- | --- |
| Initial `.venv/bin/python -m pytest -q`, before performance edits | Exit 2: collection failed on missing `migrations` / `tool_tape` modules while concurrent storage work was being written. Not attributed to performance. |
| New tests before implementation | Exit 2: `saga_trpg.presentation` did not exist yet; import failure, not behavioral evidence. |
| `.venv/bin/python -m pytest -q tests/test_performance.py` | Exit 0: **36 passed in 0.28s** after implementation and metadata limits. |
| `.venv/bin/python -m pytest -q` on final code | Exit 0: **134 passed, 1 warning in 11.24s**. Includes the new golden event-id assertion. Warning: existing Starlette TestClient/httpx deprecation. |
| `.venv/bin/python -m compileall -q src` | Exit 0. |
| `.venv/bin/python -m ruff check .` | Exit 0, all checks passed. |
| `.venv/bin/python -m ruff format --check .` | First final check found only formatting in the new report's Python example; corrected and rerun successfully. |
| `git diff --check` | Exit 0. |
| `PYTHONPATH=src .venv/bin/python scripts/export_schemas.py` | Exit 0. Focused tests independently compared both exported schema objects to the models. |
| Negative control: direct `DialogueMetadata.model_validate_json` with schema version 2 | Exit 1, `ValidationError` at `schema_version`, `literal_error`, expected 1. The actual validator was reached. |
| Input stability around final full suite | All **67** Python/config/schema input hashes unchanged before/after checks. Manifest SHA-256: `114f28677f9adfb8ff42c5032157544688b20d98757d4df7eb6681b18a527f29`. |

The format-only report correction followed the final full suite; no product code/schema/test
changed afterward. These are working-tree results, not verification of baseline HEAD, a
committed release, a package artifact, or the independently moving deployment environment.

### Claim-level evidence

| Claim | Entry point and observation | Verdict |
| --- | --- | --- |
| Disabled performance leaves text alone | `present_narrative` returns original text, zero events and zero consumer calls; monkeypatched segmenter would fail if called. | verified |
| Invalid/missing metadata fails open | Real segmenter/helper, malformed/partial JSON, extra fields, wrong versions/types, overlapping/reversed/out-of-range spans and size limits; complete original text remains. | verified |
| Canonical identity is conserved | Exact roster matching for player/NPC/companion; unknown/case-mismatched ids become unresolved; explicit narrator remains distinct. | verified within caller-supplied roster contract |
| Projection can be retried safely | Same inputs produce same ids, golden serialization contract and model JSON round-trip; idempotent fake consumer upserts three deliveries into one item. | verified local contract; durable delivery unverified |
| Presentation has no game mutation effect | SQLite game entry -> fallback narrator -> helper -> mutation-attempt consumer; state/history reread each time and after a new repository instance; replay equal, dice counter unchanged. | verified |
| Consumer failure is optional | Exception and cooperative timeout retain text; one attempt, no private exception text in logs; task cancellation propagates. | verified |
| Exported contracts match source | Actual saved JSON schemas loaded and compared to model schemas. | verified |
| Reddit audiovisual quality, live host/providers, durable media replay | Not executed. | unverified |

Final source hashes for this increment:

| File | SHA-256 |
| --- | --- |
| `presentation/models.py` | `d0a89302ed521111e6d1ea95253c7d1ad2dd704591e7cacac7048d3ed3d04a53` |
| `presentation/segmentation.py` | `45b95082879244d5cd3ecf2ed8638e911a15a241c1393a6ec7a4e2c9a397caea` |
| `presentation/service.py` | `056be41443dda4e07fd26449888d21651fd2c06e52e201f3bbb69ec73b92a8c9` |
| `ports/performance.py` | `715fb2430b13638a8b4524094ed1dfd69331617312708362e3df2169f9637bfa` |
| `tests/test_performance.py` | `63a5132dc1ac0ed52e410b5da6372b7a738d2d8a542a492cc0d83374c75c61e9` |

The three presentation paths are relative to `src/saga_trpg/`.

## Architecture self-review

- Deterministic domain/runtime modules were not edited for performance. Segmentation reads no
  clock, filesystem, network, randomness or provider. The deadline/logging live in the outer helper.
- No performance field enters `WorldState`, `GameEvent`, hashes, journal, snapshot, rules or
  benchmark mechanics. No dice/tool/memory capability is passed to a consumer.
- Rerender/replay checks reread committed results; the prototype never calls dispatch.
- NMOS remains an optional `MemoryPort` dependency; no alternate memory system was added.
- No external endpoint, key, model name, provider-specific field or database table was added.
- Source identifiers, audience filtering and persistence remain host responsibilities. Frozen
  DTOs prevent ordinary assignment; trusted in-process adapters are not sandboxed by Python types.

## Not verified and risks

- Natural-language dialogue extraction and semantic correctness of hints are not implemented.
  Plain prose remains unclassified unless a valid sidecar identifies dialogue spans.
- The host must bind renditions to real committed events, supply approved actor ids from that
  event's snapshot, convert JS UTF-16 offsets, and display text before awaiting optional work.
- Consumers must deduplicate effects; cancellation/deadlines are cooperative. No crash-safe
  delivery, remote exactly-once behavior, persistent presentation or audiovisual replay is claimed.
- Existing narrator requests contain full detached results; an audience/privacy projection is
  required before real external enrichment. Detachment alone is not confidentiality.
- Provider reliability, performance/latency and cost have not been benchmarked. Reference source
  observations are not live SillyTavern tests; the Reddit video itself was not played.

## Next recommended step

Within the existing RisuAI integration work, bind host rendition ids to committed SAGA events
and verify immediate text visibility plus optional consumption. Follow the design's phase 2
archive/streaming gates before promising reconnect playback. Keep NMOS boundary work and
P0/P1 priorities intact; do not start with paid image/TTS calls.
