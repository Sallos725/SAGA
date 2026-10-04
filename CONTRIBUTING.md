# Contributing to SAGA

SAGA is a prototype focused on deterministic state, replay, and durable tool results.
Read the [README](README.md) ([한국어](README.ko.md)), [architecture](docs/ARCHITECTURE.md),
and [implementation plan](docs/IMPLEMENTATION_PLAN.md) before proposing a change.
Repository instructions live in [AGENTS.md](AGENTS.md); the
[agent handoff](docs/AGENT_HANDOFF.md) describes the current integration work.

## Report a problem or propose a feature

Use the repository's [issue templates](https://github.com/Sallos725/SAGA/issues/new/choose).
Reports and proposals may be written in Korean or English.

- For bugs, include the revision, Python version, minimal reproduction, and expected/actual behavior.
- For features, explain the use case, the smallest useful change, and how it fits the current roadmap.
- Use synthetic campaign data in reproductions. Remove credentials and private campaign content from logs.

## Set up a development environment

Use Python 3.11–3.13. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,api]'
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

Run focused checks while working. Before submitting implementation changes, run the full CI checks:

```bash
pytest
ruff check .
ruff format --check .
python -m compileall src
```

For documentation-only changes, check links, examples, and `git diff --check`.
Keep the English and Korean READMEs aligned in the same change; preserve command and API identifiers.

## Preserve the runtime boundary

- Keep domain models and deterministic runtime code free of network/filesystem I/O, wall-clock reads, and unseeded randomness.
- Validate LLM-derived intent as a `GameEvent` before dispatch. LLMs never mutate authoritative state directly.
- Access semantic memory through `MemoryPort`; exact game facts remain in authoritative state.
- Route non-pure external tools through the durable result tape. Never automatically rerun an ambiguous pending execution.
- Reuse committed transitions and roll records for replay and narration regeneration.
- Keep adapters thin and benchmark code separate from production orchestration.

See [AGENTS.md](AGENTS.md) for the complete invariants and definition of done.

## Submit a pull request

Keep changes small and focused. Describe the problem, resulting behavior, verification, and any remaining limits.
Add behavior tests for changed runtime invariants, and document failure behavior and public contract changes.
Changes to event semantics require explicit schema/runtime versioning.
For ruleset source material, follow the existing [attribution and license guidance](docs/RULESET_LICENSES.md).
