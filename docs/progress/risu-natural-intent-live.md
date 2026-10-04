# Natural attack and regeneration on 6011

On 2026-10-04, the SAGA backend and plugin 0.4.0 were deployed to the existing 6011
PocketRisu host. A fresh chat accepted `상대를 공격한다`, then regenerated that response
once with new Gemini prose and unchanged authoritative results. This verifies one natural
attack and its regeneration, not general natural-language quality or all P1 work.

## Approval and deployed scope

The user explicitly approved deployment and this test after being told the maximum was
three generation attempts: initial intent plus narration, then one narration-only regeneration.
The estimate was US$0.03–0.07 with US$0.10 planning headroom, **not a guaranteed dollar cap**.
The test would stop at the first failure, refusal or timeout. Both requests succeeded; no
additional model tests were run. Further paid tests require a new scoped approval.

The backend matched all 76 executable-file hashes in the reviewed snapshot. The installed
SAGA plugin reported version 0.4.0 and SHA-256
`7397a2cccb83287571eaf33291ff91165e80d8f3698d0d86aded4fe467603408`.
The runtime image was
`sha256:4b4c54b27e37dd830ba4d573700026ca567f7a6f3109b1d9525bb17423a2ce98`;
the previous runtime image and database were backed up before the update.

PocketRisu's core container and image were unchanged. Existing settings/chats and the
user-entered Vertex JSON were preserved inside the host; the credential contents were not
exported. Only the backend and plugin were updated. The host retained its existing card;
the repository's 0.4.0 starter card was **not** imported in this test.

## Observed result

The test used a fresh host chat, `New Chat7`, with the existing card and Vertex configuration.

| Phase | Observed result |
| --- | --- |
| Send `상대를 공격한다` | HTTP 200, real intent and narration path, displayed prose, no fallback |
| Regenerate once | HTTP 200, different displayed prose, no fallback |
| Authoritative comparison | Same event id, state, rolls, journal and interpretation tape |
| Persisted records | One committed journal entry; one completed attack interpretation |
| Final HP | player 20/20; opponent 21/30, unchanged by regeneration |

The attack hit for 9 damage. Persisted records were reread after both requests; regeneration
retained the same sequence-1 event and tape digest. The displayed prose changed from 50 to
55 characters. A subsequent browser reload and completed UI chat load retained plugin 0.4.0
with the same script hash, the configured credential, both test messages and the regenerated
record-reuse display, with zero additional generation calls (`reload-check.json`).
This checks the actual host/provider path in addition to the earlier offline
[implementation and integration tests](risu-natural-intent.md).

Two host requests were observed, and the full three-generation budget was reserved. One
intent and two narration calls are inferred from those requests, the verified no-retry code
and the unchanged completed interpretation tape. This was **not** a direct count from
provider billing telemetry. Actual billed cost and token usage were not observed; the cost
estimate is not an invoice or measured charge. No follow-up allowance remains from this test.

## Evidence and limits

Sanitized task evidence is retained outside the repository under
`/tmp/saga-natural-intent-live-evidence/`: `approval.json` records the pre-test approval;
`backend-deployment.json` and `plugin-installed.json` record the deployed boundary;
`committed-baseline.json` records the first result and persisted state; `live-result.json`
records both HTTP/UI outcomes and the unchanged state/journal/tape comparison;
`reload-check.json` records persistence after browser reload. The approval
file's initial zero-attempt counter is a pre-test snapshot, not a remaining-call allowance.

The live evidence covers only this single Korean attack wording and one regeneration.
Natural-language checks/saves, other wording, ambiguity/refusal quality, live failure recovery,
and importing the new starter card remain unverified. Their implemented behavior has offline
coverage where described in the implementation report. NMOS, broader card/lore compatibility,
streaming, pending-execution reconciliation and the 256-action limit remain separate work.
This result does not mark P1 complete. This documentation follow-up changed no executable
code and performed no additional model calls, commit, push or PR.
