# Architecture

## System boundary

```text
RisuAI / CLI / benchmark harness
             |
             v
      outer orchestration
      |      |       |
      |      |       +------> Narrator LLM
      |      +--------------> optional MemoryPort (null / fake / NMOS / other)
      +---------------------> Intent interpreter
             |
             v
       validated GameEvent
             |
             v
      Deterministic Runtime
       |        |        |
       |        |        +--> Rule modules
       |        +-----------> Dice/Tool ports
       +--------------------> Event Journal
             |
             v
      committed RuleResult
             |
             +--> memory write(s), fail-soft
             +--> narration request
             +--> UI/status
```

## Responsibility matrix

| Concern | Owner | Must not own |
| --- | --- | --- |
| HP, inventory, quest stage, exact conditions | Runtime state | NMOS / narrator |
| Semantic long-term recall | Optional MemoryPort adapter | Runtime rule code |
| Dice/tool result | Tool-result tape + adapter + journal | Narrator |
| Free-text interpretation | Intent adapter | Rule mutation |
| Prose | Narrator | State mutation |
| Reroll/regeneration | Outer adapter | New state/tool execution |
| Reproducibility | Journal + scenario + seed/config | Prompt alone |

## Event lifecycle

1. Outer adapter receives user action.
2. Free text is interpreted into a closed `GameEvent` proposal.
3. Runtime validates and assigns/reuses a stable event id.
4. Rule module reads current `WorldState` and may call tool ports.
5. External tool calls are durably claimed and recorded by the outer adapter before use.
   Rules consume structured results (`RollRecord` for dice); they do not invoke remote I/O
   directly. Local deterministic dice retain their pure execution path.
6. Runtime produces `RuleResult` with new state, facts, patches, trace, and rolls.
7. Journal persists the committed event/result before the host exposes the new state.
8. Memory write is derived from committed facts. Failure is logged but does not roll back valid game state unless a future design explicitly requires transactional memory.
9. Narrator sees a read-only snapshot: committed facts, relevant memories, recent dialogue, card/lore context.
10. Regeneration reuses step 7 and only repeats narration.

## Authoritative state vs semantic memory

Good runtime state:

- `player.hp = 17`
- `inventory.silver_key = 1`
- `quest.black_tower.stage = 4`
- `sera.position = "west_gate"`
- `poisoned = true`

Good NMOS memory:

- "Sera warned the player that the western drainage tunnel is unstable."
- "The innkeeper believes the player still owes 30 silver."
- "Doyun witnessed the player giving Sera the necklace."
- "The party previously failed to persuade the gate captain."

Some facts may exist in both layers for convenience, but if exact correctness matters the runtime wins.

## Epistemic visibility

Do not confuse world truth with who knows the truth.

Suggested model:

- Runtime stores visibility/knowledge edges required for rule correctness: witnessed event ids, disclosed secret ids, current presence, etc.
- NMOS stores/retrieves the semantic content scoped by world and actor knowledge.
- A narrator for NPC `sera` may retrieve only memories visible to `sera`.
- Hidden benchmark checks are never exposed through NMOS or narrator context.

## Reroll semantics

Two operations must be distinct:

- `retry action`: new event, may produce new tool outcomes depending on rules.
- `rerender narration`: same committed event, no state change, no tool call.

RisuAI integration must map its regeneration behavior to `rerender narration` whenever the underlying user action did not change.

## Branching

A campaign branch can be represented as:

- base snapshot/event prefix
- branch id
- subsequent append-only journal entries

Never mutate historical event results in place. Edited user turns should create a new branch or truncate/rebuild from a known prefix with explicit provenance.

## Current persistence boundary

The PocketRisu command adapter is an outer client of that boundary. It derives a world from
host-instance/chat ids and immutable action branches from message ids, raw text and parent
events. Committed entries are read before intent interpretation on regeneration. No domain
or runtime I/O, state schema, or database schema was changed for this bridge. See
[the Risu integration contract](RISUAI_INTEGRATION.md) for its bounded demo scope.

`adapters/storage/sqlite.py` owns filesystem/database I/O and serializes each dispatch in a
transaction before invoking the existing runtime. Missing external results suspend a rule
attempt without committing its partial state. The repository durably reserves the event,
executes through `ToolResultTapePort` outside the world transaction, and reevaluates from the
same state with recorded results. The final journal/snapshot/tape-status commit is atomic.
Other actions cannot alter a branch reserved by an unresolved event. Pending tool claims
are never expired or blindly rerun; remote reconciliation is an explicit later integration.
The HTTP host uses this repository instead
of a process-local registry. The adapter stores full committed results and periodic snapshots;
recovery and branching replay these results without executing rules or tools. Forks copy their
immutable prefix and record parent provenance. See [PERSISTENCE.md](PERSISTENCE.md) for storage
versions, API routes, retention, and failure-window limitations.

Storage migration v1 → v2 adds tape metadata without changing event/state schema v1 or
runtime compatibility version `0.2.0`. It backs up the source and verifies replay and original
history before committing. See [ADR 0005](adr/0005-durable-tool-results.md).

## System-sized example modules

A campaign selects one registered ruleset id/version, such as `srd-5.2.1` or `fate-condensed`.
Each example definition provides a stateless rule handler, closed event payload schemas,
source/license metadata, an explicit implementation boundary, and narrator instructions.
The common bot description holds only shared GM/runtime behavior. Module descriptions are
prompt context, while the selected handler owns actual mechanics. The persistent world row
pins selection; neither chat text nor event payloads replace it. These examples implement
declared subsets, not entire rulebooks. See [the module packs](../packs/rulesets/README.md).

## Optional narrative performance

The local `presentation/` foundation projects completed narrative text and optional validated
dialogue spans into versioned performance events. Canonical speaker ids come from the
committed snapshot's approved actor roster. It is disabled by default, has no provider calls
or persistence, and is not wired into the debug HTTP host. Consumer failures preserve text.
See [the investigation](PERFORMANCE_LAYER.md) and
[ADR 0004](adr/0004-optional-performance-projection.md) before adding inference, TTS, visuals,
streaming or an archive. Presentation never becomes a game event or a replay prerequisite.
