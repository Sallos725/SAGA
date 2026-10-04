import json
from pathlib import Path

import pytest

from nmos_trpg.adapters.narrator.fallback import FallbackNarrator
from nmos_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import WorldState
from nmos_trpg.domain.rulesets import RulesetManifest
from nmos_trpg.ports.narrator import NarrationRequest


@pytest.mark.parametrize("ruleset_id", ["srd-5.2.1", "fate-condensed"])
@pytest.mark.asyncio
async def test_exported_pack_runs_and_rerenders_after_restart(tmp_path, ruleset_id):
    directory = Path(__file__).resolve().parents[1] / "packs" / "rulesets" / ruleset_id
    manifest = RulesetManifest.model_validate_json((directory / "module.json").read_text())
    world = WorldState.model_validate_json((directory / "initial-world.json").read_text())
    events = [
        GameEvent.model_validate(item)
        for item in json.loads((directory / "events.json").read_text())
    ]
    assert manifest.attribution in (directory / "prompt.md").read_text()
    repository = SQLiteWorldRepository(tmp_path / "packs.sqlite3", snapshot_interval=2)
    repository.create_world(
        world, ruleset_id=manifest.ruleset_id, ruleset_version=manifest.ruleset_version
    )
    for event in events:
        repository.dispatch(world.world_id, event)
    state = repository.get_state(world.world_id)
    entries = repository.entries(world.world_id)

    class NeverDice:
        def roll(self, **kwargs):
            raise AssertionError("recorded actions and narration must not reroll")

    restarted = SQLiteWorldRepository(repository.path, dice=NeverDice())
    assert restarted.replay(world.world_id) == state
    for _ in range(10):
        for event in events:
            outcome = restarted.dispatch(world.world_id, event)
            assert outcome.replayed
            request = NarrationRequest(
                world_id=world.world_id,
                event=outcome.entry.committed.event,
                result=outcome.entry.result,
            )
            assert await FallbackNarrator().render(request)
    assert restarted.get_state(world.world_id) == state
    assert restarted.entries(world.world_id) == entries
