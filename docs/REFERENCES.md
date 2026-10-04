# References for implementers

These are architectural references, not dependencies by default.

## Aethrion

Repository: https://github.com/simulacre7/aethrion

The project owner identifies Aethrion as an original inspiration for SAGA.
Source reviewed on 2026-10-04 at commit
[`708f60739f13b5cbac9b17de13d3a6873101c2d8`](https://github.com/simulacre7/aethrion/tree/708f60739f13b5cbac9b17de13d3a6873101c2d8).
The review inspected source and tests; it did not run Aethrion or verify a live RisuAI client.

### License and reuse

The checked [LICENSE](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/LICENSE)
is MIT, copyright 2026 simulacre7. It permits use, modification, and distribution,
including commercial use, subject to retaining its copyright and permission notice
in copies or substantial portions. When copying or adapting source, preserve the full
license notice alongside the reused material and record its upstream path and revision.
This reference review imports no upstream code and does not change SAGA's license.
Third-party game rules and assets still require their own license review; see
[RULESET_LICENSES.md](RULESET_LICENSES.md).

### Useful implementation references

| Reference | Observed behavior | Application to SAGA |
| --- | --- | --- |
| [RisuAI bridge](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/lib/aethrion/bridge.ex), [integration guide](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/docs/risuai.md) | Extracts player turns, caches interpretations, and carries checkpoint ids in generated status blocks; handles edits, continuation, and trimmed history. | Use as a reference for the outer chat adapter's turn mapping and status display. Prefer stable client ids when available; verify the actual client's requests before choosing a fallback. |
| [Expression request](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/lib/aethrion/expression/request.ex) | Packages speaker/listener context, selected memories, simulated time, and fallback text for narration. | Build narration from a detached, visibility-filtered view of a committed result. The current SAGA request carries a full `RuleResult`; an NPC-facing projection remains future work. |
| [Interpreter](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/lib/aethrion/interpreter.ex) | Supplies available actions and targets, then validates proposed events and handles invalid interpretations. | Ground `IntentPort` in the selected ruleset's closed action schemas. Never apply a proposed numeric state change directly. |
| [Observation](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/lib/aethrion/rules/observation.ex), [gossip](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/lib/aethrion/rules/gossip.ex) | Records observers and distinguishes witnessed events from information passed between characters. | Keep rule-relevant knowledge/provenance in authoritative state and scope semantic recall through `MemoryPort`. Social mechanics are a later feature, not implicitly part of every ruleset. |
| [Bridge tests](https://github.com/simulacre7/aethrion/blob/708f60739f13b5cbac9b17de13d3a6873101c2d8/test/aethrion/bridge_test.exs) | Contains cases for regeneration, edited turns, and truncated chat history. | Reuse these scenarios as acceptance-test ideas, adding assertions for unchanged journal length and zero repeated dice/tool calls. |

### Adaptation boundaries

- Aethrion's bridge can reconstruct state by executing rules over transcript turns after
  a matching checkpoint. SAGA must reuse committed journal entries and roll records on
  regeneration and replay; deterministic recomputation alone does not satisfy its tool
  execution contract. Edited turns should fork from the matching committed prefix.
- Transcript hashes or a status marker alone do not establish campaign ownership.
  Bind any checkpoint lookup to the resolved world/branch and ruleset version, and do
  not merge separate campaigns merely because their dialogue matches. Missing or
  ambiguous identity needs explicit handling before dispatch.
- Keep semantic memory retrieval and consolidation behind NMOS's `MemoryPort`.
  Aethrion's internal memory subsystem is not a replacement to import into SAGA.
- Retain one selected game-system module per campaign. Any useful internal rule
  ordering can remain inside that module; users need not assemble small rule plugins.

The nearest implementation opportunity is the planned RisuAI vertical slice:
turn identity, committed-result narration, and regeneration/edit/continuation tests.
These references guide that work; they do not make the current RisuAI placeholder
an implemented integration.

## oneuldo memory pilot

The owner's `Sallos725/oneuldo` branch `codex/memory-pilot-6010` supplies a concrete
PocketRisu plugin reference for reading chat/message ids, synchronizing selected replies,
and injecting current actor knowledge before generation. See
[the pinned source review and reproduced tests](RISUAI_INTEGRATION.md#host-identity-reference-oneuldo-memory-pilot).
Use this host boundary when implementing SAGA's turn mapping; its full-snapshot revision
and assistant-derived memory projection are not SAGA's action/event semantics.

## Local RisuAI dice module and plugin

The owner supplied `D&D Dice v2.0.0.risum` and `dnd-dice-mcp-v3_2.1.0.js` under
`~/saga-ref/dice/`. See [the local dice reference review](LOCAL_DICE_REFERENCE.md)
for source hashes, decoded module contents, tool contracts, presentation references,
and the proposed SAGA mapping. The files were inspected without executing the plugin.
The host-local MCP registration does not establish a backend transport; any live
binding must use SAGA's durable result tape and preserve ruleset-owned adjudication.

## NMOS

Repository: https://github.com/Sallos725/NMOS

Treat NMOS as an external long-horizon memory system. Inspect its current API/contracts at implementation time. Do not create a frozen copy of NMOS inside this project merely to make the adapter convenient.
