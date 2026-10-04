"""Export the optional HTTP bridge contract with the project's [api] extra installed."""

import json
from pathlib import Path

from saga_trpg.adapters.risu.openai_proxy import CompletionRequest

destination = Path(__file__).resolve().parents[1] / "schemas" / "risu-completion.schema.json"
destination.write_text(
    json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **CompletionRequest.model_json_schema(),
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
