"""Export the optional HTTP bridge contract with the project's [api] extra installed."""

import json
from pathlib import Path

from saga_trpg.adapters.risu.openai_proxy import CompletionRequest, TurnRequest

schemas_dir = Path(__file__).resolve().parents[1] / "schemas"
for filename, model in (
    ("risu-completion.schema.json", CompletionRequest),
    ("risu-turn.schema.json", TurnRequest),
):
    (schemas_dir / filename).write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                **model.model_json_schema(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
