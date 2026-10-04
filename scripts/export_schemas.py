"""Export public data contracts. Run with the project installed or PYTHONPATH=src."""

import json
from pathlib import Path

from saga_trpg.domain.branches import BranchInfo, ForkRequest
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import WorldState
from saga_trpg.domain.rulesets import (
    CreateWorldRequest,
    RulesetContext,
    RulesetManifest,
    RulesetReference,
)
from saga_trpg.domain.tools import ToolCall, ToolExecution
from saga_trpg.presentation.models import DialogueMetadata, PerformanceEvent
from saga_trpg.runtime.journal import JournalEntry


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "schemas"
    models = {
        "event": GameEvent,
        "world": WorldState,
        "journal-entry": JournalEntry,
        "fork-request": ForkRequest,
        "branch": BranchInfo,
        "create-world": CreateWorldRequest,
        "ruleset-manifest": RulesetManifest,
        "ruleset-reference": RulesetReference,
        "ruleset-context": RulesetContext,
        "tool-call": ToolCall,
        "tool-execution": ToolExecution,
        "dialogue-metadata": DialogueMetadata,
        "performance-event": PerformanceEvent,
    }
    for name, model in models.items():
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }
        (destination / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
