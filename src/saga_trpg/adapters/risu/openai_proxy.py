"""RisuAI/OpenAI-compatible orchestration placeholder.

Implement `/v1/chat/completions` as an OUTER adapter, not as game logic.

Expected flow:
1. Identify world/session and latest user line.
2. Retrieve relevant memories (optional/fail-soft).
3. Interpret free text into a closed GameEvent proposal.
4. Dispatch exactly once through RuntimeEngine.
5. Retrieve the committed JournalEntry.
6. Narrate the committed facts + selected memory.
7. On RisuAI regeneration, identify the already-committed event and only repeat step 6.

Do not infer regeneration by fragile prose matching if RisuAI exposes a stable request/message id.
Document whichever identifier is used.
"""
