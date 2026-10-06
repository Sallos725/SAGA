"""Export reviewable module source packs; these are not RisuAI-native import files."""

import argparse
import json
from pathlib import Path

from saga_trpg.domain.events import GameEvent
from saga_trpg.modules.catalog import COMMON_BOT_DESCRIPTION, RulesetRegistry
from saga_trpg.modules.srd_5_2_1.rules import LATEST_VERSION as SRD_LATEST


def json_text(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def build_files() -> dict[Path, str]:
    root = Path(__file__).resolve().parents[1] / "packs" / "rulesets"
    files = {root / "common-bot-description.md": COMMON_BOT_DESCRIPTION}
    # Packs describe the latest registered version; older versions stay pinned in the catalog.
    versions = {"srd-5.2.1": SRD_LATEST, "fate-condensed": "0.1.0"}
    samples = {
        "srd-5.2.1": [
            (
                "ability_check",
                {"ability": "strength", "difficulty": 12, "proficiency": "athletics"},
            ),
            ("saving_throw", {"ability": "strength", "difficulty": 15}),
            ("attack", {"target_id": "opponent"}),
            ("damage", {"target_id": "player", "amount": 4}),
            ("apply_condition", {"target_id": "player", "condition": "poisoned"}),
            ("advance_time", {"amount": 1, "unit": "hour"}),
            ("remove_condition", {"target_id": "player", "condition": "poisoned"}),
            ("heal", {"target_id": "player", "amount": 6}),
        ],
        "fate-condensed": [
            ("overcome", {"skill": "investigate", "difficulty": 2}),
            (
                "create_advantage",
                {"skill": "notice", "difficulty": 1, "aspect_name": "Mapped escape route"},
            ),
        ],
    }
    registry = RulesetRegistry()
    for ruleset_id, actions in samples.items():
        definition = registry.get(ruleset_id, versions[ruleset_id])
        manifest = definition.manifest
        directory = root / ruleset_id
        files[directory / "module.json"] = json_text(manifest.model_dump(mode="json"))
        files[directory / "initial-world.json"] = (
            definition.example_world(f"{ruleset_id}-example").model_dump_json(indent=2) + "\n"
        )
        events = [
            GameEvent(
                event_id=f"example-{index}",
                actor_id="player",
                kind=kind,
                payload=payload,
            ).model_dump(mode="json")
            for index, (kind, payload) in enumerate(actions, 1)
        ]
        files[directory / "events.json"] = json_text(events)
        files[directory / "prompt.md"] = (
            f"# {manifest.title}\n\n{manifest.narrator_instructions}\n"
            f"## Scope\n\nThis is a partial example implementation.\n\n"
            f"Implemented: {'; '.join(manifest.implemented)}.\n\n"
            f"Not implemented: {'; '.join(manifest.not_implemented)}.\n\n"
            f"## Source and attribution\n\n{manifest.attribution}\n\n"
            f"Source: {manifest.source_url}\n\n"
            f"License: {manifest.license_id} — {manifest.license_url}\n\n"
            f"Changes: {manifest.modifications}\n"
        )
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if exported files are stale")
    args = parser.parse_args()
    for path, content in build_files().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"stale or missing ruleset pack: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
