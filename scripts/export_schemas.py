"""Export public data contracts. Run with the project installed or PYTHONPATH=src."""

import json
from pathlib import Path

from nmos_trpg.domain.branches import BranchInfo, ForkRequest
from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import WorldState
from nmos_trpg.domain.rulesets import (
    CreateWorldRequest,
    RulesetContext,
    RulesetManifest,
    RulesetReference,
)
from nmos_trpg.runtime.journal import JournalEntry


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
