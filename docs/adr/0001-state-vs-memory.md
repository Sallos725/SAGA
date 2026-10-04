# ADR 0001: Separate authoritative state from semantic memory

Status: Accepted

## Decision

Exact game state belongs to the runtime. Long-horizon semantic recall belongs to NMOS through `MemoryPort`.

## Rationale

Retrieval systems may abstain, rank imperfectly, summarize, consolidate, or become temporarily unavailable. Those are acceptable properties for semantic recall and unacceptable properties for HP, item counts, quest stages, and other exact state.

## Consequence

Some events may produce both a state mutation and a memory write. The runtime result remains authoritative if the two ever disagree.
