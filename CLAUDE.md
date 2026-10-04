# CLAUDE.md

Read `AGENTS.md` first. It is authoritative for engineering constraints.

When working in this repository:

- Treat the runtime as a deterministic state machine.
- Treat NMOS as an external semantic-memory service, not as game state.
- Treat LLMs as untrusted interpreters/narrators at the boundary.
- Preserve replayability and idempotency before optimizing convenience.
- Prefer a boring, testable implementation over a clever framework.

Before finishing a task, explicitly inspect whether your changes could:

1. execute dice/tools twice on a rerender,
2. let narration mutate state,
3. make replay depend on wall-clock time,
4. duplicate NMOS responsibilities,
5. make benchmark runs non-comparable.

If any answer is yes, fix the design before finishing.
