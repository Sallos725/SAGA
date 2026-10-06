"""Carry a rulebook inside a RisuAI module (the host's plain JSON import shape).

See docs/RISUAI_MODULES.md for what the host accepts and reads. The module holds two lorebook
entries: narration text that is always inserted into the prompt, and the rulebook JSON in an
entry whose regex key never matches, so it travels with the module but never reaches the model.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from saga_trpg.rulebooks.loader import RulebookSpec, parse_rulebook

NAMESPACE = "saga"
NEVER_MATCHING_KEY = "(?!)"
_RULEBOOK_COMMENT = re.compile(
    r"^saga:rulebook:(?P<id>[a-z0-9][a-z0-9.-]{0,60})@(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"
    r":(?P<sha>[0-9a-f]{64})$"
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def narration_text(spec: RulebookSpec) -> str:
    implemented = "; ".join(spec.implemented) or "see the rulebook"
    not_implemented = "; ".join(spec.not_implemented) or "none listed"
    return (
        f"{spec.narration.rstrip()}\n\n"
        f"Implemented: {implemented}.\n"
        f"Not implemented: {not_implemented}."
    )


def stable_id(*parts: str) -> str:
    """Deterministic ids so an exported module is byte-stable for review and `--check`."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "saga:" + ":".join(parts)))


def _entry(**fields: Any) -> dict[str, Any]:
    base = {
        "key": "",
        "secondkey": "",
        "insertorder": 100,
        "comment": "",
        "content": "",
        "mode": "normal",
        "alwaysActive": False,
        "selective": False,
        "useRegex": False,
    }
    base.update(fields)
    base["id"] = stable_id("entry", base["comment"])
    return base


def build_module(rulebook_text: str, *, module_id: str | None = None) -> dict[str, Any]:
    """A `{type: "risuModule"}` document the host imports from a `.json` file."""
    spec = parse_rulebook(json.loads(rulebook_text))
    label = f"{spec.id}@{spec.version}"
    return {
        "type": "risuModule",
        "name": f"SAGA · {spec.title} {spec.version}",
        "description": (
            f"SAGA rulebook {label}. Rules are data interpreted by the SAGA runtime; "
            "this module carries narration instructions and the rulebook itself. "
            f"License: {spec.license.id}."
        ),
        "id": module_id or stable_id("module", label),
        "namespace": NAMESPACE,
        "lorebook": [
            _entry(
                comment=f"saga:narration:{label}",
                content=narration_text(spec),
                mode="constant",
                alwaysActive=True,
                insertorder=100,
            ),
            _entry(
                comment=f"saga:rulebook:{label}:{sha256_text(rulebook_text)}",
                content=rulebook_text,
                key=NEVER_MATCHING_KEY,
                useRegex=True,
                insertorder=0,
            ),
        ],
    }


@dataclass(frozen=True)
class FoundRulebook:
    rulebook_id: str
    version: str
    sha256: str
    content: str


def find_rulebook_entries(entries: list[Any]) -> list[FoundRulebook]:
    """Rulebook entries among lorebook entries, keeping only those whose hash matches."""
    found: list[FoundRulebook] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        match = _RULEBOOK_COMMENT.fullmatch(str(entry.get("comment", "")))
        content = entry.get("content")
        if match is None or not isinstance(content, str):
            continue
        if sha256_text(content) != match["sha"]:
            continue
        found.append(FoundRulebook(match["id"], match["version"], match["sha"], content))
    return found
