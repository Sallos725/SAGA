from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=lambda item: json.dumps(item))
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def stable_model_hash(model: BaseModel) -> str:
    payload = json.dumps(
        _canonical(model.model_dump(mode="python")),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
