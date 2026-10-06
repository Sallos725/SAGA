"""Imported rulebooks on disk: `<data dir>/rulebooks/<id>@<version>.json`.

A rulebook is written only after it validates and its runtime definition builds. The same
`<id>@<version>` may be registered again only with byte-identical content, so a world pinned
to it can never see different rules. Publication is an exclusive create, so two importers
racing on one version cannot both win.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from saga_trpg.adapters.storage.database import RepositoryConflict
from saga_trpg.rulebooks.loader import RulebookError, RulebookSpec, parse_rulebook


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RulebookStore:
    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def path_for(self, rulebook_id: str, version: str) -> Path:
        return self.directory / f"{rulebook_id}@{version}.json"

    def register(
        self,
        text: str,
        *,
        bundled: Mapping[tuple[str, str], str] | None = None,
        reserved: Iterable[tuple[str, str]] = (),
        build: Callable[[RulebookSpec], object] | None = None,
    ) -> tuple[RulebookSpec, bool]:
        """Validate, build, and store; returns the spec and whether it was newly written.

        `bundled` maps identities shipped with the package to their file digests; `reserved`
        lists every other identity the registry already serves (hand-written modules);
        `build` constructs the runtime definition so nothing unbuildable is ever published.
        """
        data = text.encode("utf-8")
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RulebookError(f"not valid JSON at line {exc.lineno}: {exc.msg}") from None
        spec = parse_rulebook(parsed)
        if build is not None:
            try:
                build(spec)
            except RulebookError:
                raise
            except Exception as exc:  # pydantic/model construction problems surface here
                raise RulebookError(f"rulebook cannot be loaded by the engine: {exc}") from None
        digest = sha256_bytes(data)
        label = f"{spec.id}@{spec.version}"
        known = (bundled or {}).get((spec.id, spec.version))
        if known is not None:
            if known != digest:
                raise RepositoryConflict(
                    f"{label} is a bundled rulebook; a different file may not reuse its version"
                )
            return spec, False
        if (spec.id, spec.version) in set(reserved):
            raise RepositoryConflict(f"{label} is a built-in ruleset version; choose another")
        path = self.path_for(spec.id, spec.version)
        if path.exists():
            return self._same_or_conflict(path, digest, label), False
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f".{spec.id}@{spec.version}.{uuid.uuid4().hex}.tmp"
        temporary.write_bytes(data)
        try:
            # Exclusive create: a racing importer sees the winner's file and compares to it.
            os.link(temporary, path)
        except FileExistsError:
            return self._same_or_conflict(path, digest, label), False
        finally:
            temporary.unlink(missing_ok=True)
        return spec, True

    def _same_or_conflict(self, path: Path, digest: str, label: str) -> RulebookSpec:
        existing = path.read_bytes()
        if sha256_bytes(existing) != digest:
            raise RepositoryConflict(
                f"{label} is already registered with different content; bump the version"
            )
        return parse_rulebook(json.loads(existing))

    def load_all(self) -> list[RulebookSpec]:
        if not self.directory.exists():
            return []
        specs = []
        for path in sorted(self.directory.glob("*@*.json")):
            spec = parse_rulebook(json.loads(path.read_bytes()))
            if path.name != f"{spec.id}@{spec.version}.json":
                raise RulebookError(f"{path}: file name does not match {spec.id}@{spec.version}")
            specs.append(spec)
        return specs
