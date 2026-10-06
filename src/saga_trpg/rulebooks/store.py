"""Imported rulebooks on disk: `<data dir>/rulebooks/<id>@<version>.json`.

A rulebook is written only after it validates. The same `<id>@<version>` may be registered
again only with byte-identical content, so a world pinned to it can never see different rules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from saga_trpg.adapters.storage.database import RepositoryConflict
from saga_trpg.rulebooks.loader import RulebookError, RulebookSpec, parse_rulebook
from saga_trpg.rulebooks.risu_module import sha256_text


class RulebookStore:
    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def path_for(self, rulebook_id: str, version: str) -> Path:
        return self.directory / f"{rulebook_id}@{version}.json"

    def register(
        self, text: str, *, bundled: Mapping[tuple[str, str], str] | None = None
    ) -> tuple[RulebookSpec, bool]:
        """Validate and store; returns the spec and whether it was newly written."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RulebookError(f"not valid JSON at line {exc.lineno}: {exc.msg}") from None
        spec = parse_rulebook(data)
        digest = sha256_text(text)
        label = f"{spec.id}@{spec.version}"
        known = (bundled or {}).get((spec.id, spec.version))
        if known is not None:
            if known != digest:
                raise RepositoryConflict(
                    f"{label} is a bundled rulebook; a different file may not reuse its version"
                )
            return spec, False
        path = self.path_for(spec.id, spec.version)
        if path.exists():
            if sha256_text(path.read_text(encoding="utf-8")) != digest:
                raise RepositoryConflict(
                    f"{label} is already registered with different content; bump the version"
                )
            return spec, False
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        return spec, True

    def load_all(self) -> list[RulebookSpec]:
        if not self.directory.exists():
            return []
        specs = []
        for path in sorted(self.directory.glob("*@*.json")):
            spec = parse_rulebook(json.loads(path.read_text(encoding="utf-8")))
            if path.name != f"{spec.id}@{spec.version}.json":
                raise RulebookError(f"{path}: file name does not match {spec.id}@{spec.version}")
            specs.append(spec)
        return specs
