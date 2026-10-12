# Issue tracking

SAGA uses [Plane](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/)
for issue status and priorities. Repository ADRs and progress reports remain the sources
for design decisions, supported contracts and verification evidence.

## Completed import — 2026-10-11

- Live read-back confirmed 46 project issues: 45 tracked items (SAGA-2 through
  SAGA-46), plus the unchanged pre-existing SAGA-1 `Agents`.
- The interrupted AGE-50 creation had already saved SAGA-45. Only AGE-51 was
  missing and was created as SAGA-46 after reconciliation by external source/id.
- Each of the 45 manifest items has exactly one matching
  `external_source` + `external_id`. No duplicate was found.
- 13 Done items record historical implementation evidence; 32 tracked items
  remain Backlog. SAGA-1 is also Backlog, for 33 project Backlog issues in total.
  This import did not rerun runtime tests or verify deployments.
- All tracked items are assigned to their intended module. Seven modules exist;
  maintenance has no manifest items and remains empty.
- Seven dependency edges and all seven proposal child-parent links were read
  back. SAGA-40 through SAGA-46 are children of SAGA-39.
- Original proposal content and source links were checked against the resume
  manifest. Plane collapses blank lines in `description_stripped`; comparisons
  normalized whitespace. Repository items retain scope and acceptance criteria.
- Titles, priorities and Done/Backlog status were checked against the manifest.
  SAGA-1's returned record was unchanged from the initial live query.
- [The Plane overview page](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/pages/8bf244a1-bdb6-40c8-91df-b8eff5c6ced4/)
  was created and read back with the final counts and migration mapping.
- NMOS-only [AGE-52](https://linear.app/sallos725/issue/AGE-52) remains in its
  tracker as related context. Linear originals were not modified, deleted or archived.
- Assignees, deadlines and cycles were not invented. Proposal registration does
  not authorize implementation or event/schema semantics changes.

## Verified module membership

| Module | Tracked items |
| --- | ---: |
| P0 — Runtime correctness | 4 |
| P1 — NMOS + RisuAI | 12 |
| P2 — Tools + rules | 12 |
| P3 — RuleCheeseBench | 4 |
| P4 — Authoring + presentation | 5 |
| Product proposals — TRPG features | 8 |
| Project maintenance | 0 |

## Verified dependencies

The issue in the left column is `blocked_by` the issues in the right column.
The MCP adapter requires a selected provider and its ambiguous-result recovery policy.
Live NMOS ablation requires a designated, tested sidecar, visibility contract and
long-campaign evidence. These links describe prerequisites; they do not approve live
provider usage or implementation changes.

| Issue | Prerequisites |
| --- | --- |
| [SAGA-20](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/c617ea95-fb43-4746-bb2e-9006496d177d/) (tool-reconciliation) | [SAGA-19](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/fbdef0ef-7790-43dd-b573-ad80856c0b3a/) (mcp-selection) |
| [SAGA-21](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/a51e628a-0834-4dfd-9c2b-1ffddd2a89ed/) (mcp-adapter) | [SAGA-19](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/fbdef0ef-7790-43dd-b573-ad80856c0b3a/) (mcp-selection), [SAGA-20](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/c617ea95-fb43-4746-bb2e-9006496d177d/) (tool-reconciliation) |
| [SAGA-9](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/bf952430-550c-4574-b7d9-6b2630f0ff64/) (nmos-long-campaign) | [SAGA-8](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/20624f37-7a5a-48b6-ae2a-6c2ba1a208f5/) (nmos-live) |
| [SAGA-32](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/543e1d17-67e4-4971-8660-b7897c5d10a2/) (benchmark-memory) | [SAGA-8](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/20624f37-7a5a-48b6-ae2a-6c2ba1a208f5/) (nmos-live), [SAGA-7](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/504c9ac7-b70b-488a-8205-990fb1ea4e68/) (nmos-visibility), [SAGA-9](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/bf952430-550c-4574-b7d9-6b2630f0ff64/) (nmos-long-campaign) |

## Source-to-Plane mapping

Repository items use `external_source=saga-repository`; imported proposals use
`external_source=linear`. The key below is their `external_id`.

| Source key | Plane issue |
| --- | --- |
| runtime-persistence | [SAGA-2](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/b6dfc33e-3479-40a1-8322-0c04e49d769e/) |
| runtime-migration | [SAGA-3](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/7d2f5f45-0395-4774-a72d-57148211f79f/) |
| runtime-identity-ci | [SAGA-4](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/7920233c-3596-4d0e-8856-c3c84a3f0c3b/) |
| trace-correlation | [SAGA-5](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/f002f320-a27b-43d1-8b6d-0ab4b8155cef/) |
| nmos-adapter | [SAGA-6](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/32c6cc31-e3b4-4f8b-ad66-195adc88b3fa/) |
| nmos-visibility | [SAGA-7](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/504c9ac7-b70b-488a-8205-990fb1ea4e68/) |
| nmos-live | [SAGA-8](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/20624f37-7a5a-48b6-ae2a-6c2ba1a208f5/) |
| nmos-long-campaign | [SAGA-9](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/bf952430-550c-4574-b7d9-6b2630f0ff64/) |
| risu-command | [SAGA-10](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/c6c2b8ab-d45e-44f6-bf33-a5742c1b2b53/) |
| risu-vertex | [SAGA-11](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/22a1b767-5587-4afa-8868-a4d2482e4aa6/) |
| risu-language-quality | [SAGA-12](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/dc68cbbf-2230-475b-b1af-f257b641e78d/) |
| risu-history-scale | [SAGA-13](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/d16eef88-69b5-4453-a957-4c3bb27a96f8/) |
| risu-card-lore | [SAGA-14](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/82aefc7f-8c9c-4164-b59e-2b4add30da95/) |
| risu-streaming | [SAGA-15](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/600b72e6-2950-410b-b48f-68c008569411/) |
| risu-import-live | [SAGA-16](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/560797f9-def6-4c82-af9e-1b20b35adf7a/) |
| risu-host-id | [SAGA-17](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/50cdfced-81c5-4e14-b774-dc078ac8a8cd/) |
| tool-tape | [SAGA-18](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/0ac1ab31-c0c3-46a7-a393-80f98c296bec/) |
| mcp-selection | [SAGA-19](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/fbdef0ef-7790-43dd-b573-ad80856c0b3a/) |
| tool-reconciliation | [SAGA-20](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/c617ea95-fb43-4746-bb2e-9006496d177d/) |
| mcp-adapter | [SAGA-21](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/a51e628a-0834-4dfd-9c2b-1ffddd2a89ed/) |
| tool-manifest | [SAGA-22](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/6af38e33-3c5a-4d6d-9be9-e841289bb2e3/) |
| rules-state | [SAGA-23](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/18a85516-88f6-46e5-9899-3008593daf35/) |
| rulebooks-data | [SAGA-24](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/f4117647-86cf-494c-b2ed-5ecdd5b5a8a2/) |
| rulebook-risu-export | [SAGA-25](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/62960ce1-04d7-43a6-a38d-00dcb68ea519/) |
| quest | [SAGA-26](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/a93ebf06-8cca-4c18-b6df-b4f1da061578/) |
| observation | [SAGA-27](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/e01bed31-4fd9-45fd-b19c-2c18e8ec427e/) |
| rulebook-expressiveness | [SAGA-28](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/a26a667c-06a3-4964-b444-a41ad8a8804d/) |
| rules-extensions | [SAGA-29](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/7cb60e4a-573a-47c1-8119-f535b0d78c9e/) |
| benchmark-core | [SAGA-30](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/fd046034-37fe-4bfd-80b7-2cde2a353965/) |
| benchmark-live | [SAGA-31](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/60d74d4d-d3fa-4777-9d94-beb079cce53c/) |
| benchmark-memory | [SAGA-32](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/543e1d17-67e4-4971-8660-b7897c5d10a2/) |
| benchmark-judges | [SAGA-33](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/62fb8a04-517b-4e1e-8b98-a6d18a6d84dd/) |
| author-skill | [SAGA-34](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/8cf95d70-cb48-46e3-a383-24936361e460/) |
| rulebook-generator | [SAGA-35](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/2ab3295c-1ae5-4f06-a96e-912defd64b74/) |
| performance-foundation | [SAGA-36](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/fea959c3-26ae-4d58-a785-ad85cdf601b6/) |
| performance-host | [SAGA-37](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/fdb3a765-c04f-4413-bbe6-e84f5d48f44c/) |
| performance-media | [SAGA-38](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/2ed37d7d-c552-4be6-979f-62475dd27217/) |
| AGE-44 | [SAGA-39](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/ceeb5967-23f1-41a8-b913-0b06b9add268/) |
| AGE-45 | [SAGA-40](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/26e369a5-5840-4c12-b9fb-d6057d8a1b6d/) |
| AGE-46 | [SAGA-41](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/c7331312-d3e0-49de-9fee-32e7a3525062/) |
| AGE-47 | [SAGA-42](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/5b74b21a-02f9-48cf-83e4-8cf40f05a1cb/) |
| AGE-48 | [SAGA-43](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/4a74d4e7-3937-42ca-acad-6da95024e5ca/) |
| AGE-49 | [SAGA-44](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/cc780fc2-1b0b-419d-90e1-c2c69416c523/) |
| AGE-50 | [SAGA-45](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/678a4b50-7303-4a2b-be55-44170a7e803b/) |
| AGE-51 | [SAGA-46](https://app.plane.so/sallos/projects/bce65fff-ee0a-4677-b068-b255ed9eebfe/issues/9a788dde-ada5-4647-981f-195eb27ab4a1/) |

## Remaining product work and verification limits

The import has no remaining registration, module assignment, dependency or overview
work. Open product work remains in the Backlog issues. Historical Done is not a
claim of current deployment or live-provider verification.

The earlier host approval interruption is historical; the operations above succeeded
in this continuation. Permission settings were not changed here, and behavior in
future chats has not been established. No code, runtime schema, deployment, secret or
paid provider test changed. No commit was made.

`/tmp/saga-plane-resume.json` is a temporary handoff artifact, not a committed project
dependency. This document is the durable import audit.
