import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from saga_trpg.adapters.storage.sqlite import RepositoryConflict, SQLiteWorldRepository
from saga_trpg.api.app import create_app
from saga_trpg.rulebooks.loader import RulebookError, load_rulebook, rulebook_path
from saga_trpg.rulebooks.risu_module import (
    build_module,
    find_rulebook_entries,
    narration_text,
)
from saga_trpg.rulebooks.store import RulebookStore

SRD_TEXT = rulebook_path("srd-5.2.1").read_text(encoding="utf-8")
FATE_TEXT = rulebook_path("fate-condensed").read_text(encoding="utf-8")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- export: rulebook -> RisuAI module JSON ------------------------------------------------


def test_module_json_is_the_plain_import_shape_with_two_lorebook_entries():
    module = build_module(SRD_TEXT)
    assert module["type"] == "risuModule"
    assert module["name"] == "SAGA · SRD 5.2.1 example 0.4.0"
    assert module["id"] and module["namespace"] == "saga"
    assert set(module) == {"type", "name", "description", "id", "namespace", "lorebook"}
    narration, data = module["lorebook"]
    assert narration["comment"] == "saga:narration:srd-5.2.1@0.4.0"
    assert narration["alwaysActive"] is True and narration["mode"] == "constant"
    assert narration["content"] == narration_text(load_rulebook(rulebook_path("srd-5.2.1")))
    assert "Not implemented:" in narration["content"]
    assert data["comment"] == f"saga:rulebook:srd-5.2.1@0.4.0:{sha(SRD_TEXT)}"
    assert data["useRegex"] is True and data["key"] == "(?!)"
    assert data["alwaysActive"] is False and data["mode"] == "normal"
    assert data["selective"] is False and data["secondkey"] == ""
    assert data["content"] == SRD_TEXT
    for entry in (narration, data):
        assert entry["id"] and entry["insertorder"] >= 0


def test_module_round_trips_through_the_lorebook_finder():
    module = build_module(FATE_TEXT)
    entries = module["lorebook"] + [
        {"comment": "unrelated", "content": "x", "key": "a", "alwaysActive": True},
        {"comment": "saga:rulebook:broken", "content": "{}"},
    ]
    found = find_rulebook_entries(entries)
    assert [(item.rulebook_id, item.version, item.sha256) for item in found] == [
        ("fate-condensed", "0.2.0", sha(FATE_TEXT))
    ]
    assert found[0].content == FATE_TEXT
    assert load_rulebook_text(found[0].content).id == "fate-condensed"


def load_rulebook_text(text):
    from saga_trpg.rulebooks.loader import parse_rulebook

    return parse_rulebook(json.loads(text))


def test_module_command_writes_a_file(tmp_path, capsys):
    from saga_trpg.rulebooks.__main__ import main

    out = tmp_path / "srd.module.json"
    assert main(["module", str(rulebook_path("srd-5.2.1")), "--out", str(out)]) == 0
    module = json.loads(out.read_text(encoding="utf-8"))
    assert module["type"] == "risuModule" and len(module["lorebook"]) == 2
    assert str(out) in capsys.readouterr().out
    assert main(["module", str(tmp_path / "missing.json"), "--out", str(out)]) == 1


# --- import: backend store -------------------------------------------------------------------


def test_store_persists_validated_rulebooks_and_refuses_conflicting_content(tmp_path):
    store = RulebookStore(tmp_path / "rulebooks")
    spec, created = store.register(FATE_TEXT)
    assert created and (spec.id, spec.version) == ("fate-condensed", "0.2.0")
    assert (tmp_path / "rulebooks" / "fate-condensed@0.2.0.json").read_text("utf-8") == FATE_TEXT
    spec_again, created_again = store.register(FATE_TEXT)
    assert not created_again and spec_again.version == "0.2.0"
    changed = FATE_TEXT.replace('"title": "Fate Condensed example"', '"title": "Fate (edited)"')
    with pytest.raises(RepositoryConflict, match="fate-condensed@0.2.0"):
        store.register(changed)
    with pytest.raises(RulebookError):
        store.register("{not json")
    with pytest.raises(RulebookError, match="actions"):
        store.register(json.dumps({"schema_version": 1, "id": "x", "version": "0.1.0"}))
    assert [
        (item.id, item.version) for item in RulebookStore(tmp_path / "rulebooks").load_all()
    ] == [("fate-condensed", "0.2.0")]


def test_store_refuses_to_shadow_a_bundled_rulebook_version(tmp_path):
    store = RulebookStore(tmp_path / "rulebooks")
    changed = SRD_TEXT.replace('"title": "SRD 5.2.1 example"', '"title": "SRD (edited)"')
    with pytest.raises(RepositoryConflict, match="bundled"):
        store.register(changed, bundled={("srd-5.2.1", "0.4.0"): sha(SRD_TEXT)})
    spec, created = store.register(SRD_TEXT, bundled={("srd-5.2.1", "0.4.0"): sha(SRD_TEXT)})
    assert not created and spec.version == "0.4.0"
    assert not (tmp_path / "rulebooks" / "srd-5.2.1@0.4.0.json").exists()


def client_for(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "worlds.sqlite3")
    app = create_app(repository=repo)
    return repo, TestClient(app)


def test_api_imports_a_rulebook_and_pins_worlds_to_it_across_restart(tmp_path):
    repo, client = client_for(tmp_path)
    custom = json.loads(FATE_TEXT)
    custom["id"], custom["version"], custom["title"] = "house-fate", "1.0.0", "House Fate"
    text = json.dumps(custom)
    response = client.post("/rulebooks", json={"content": text})
    assert response.status_code == 201, response.text
    assert response.json() == {
        "ruleset_id": "house-fate",
        "ruleset_version": "1.0.0",
        "sha256": sha(text),
        "created": True,
    }
    assert client.post("/rulebooks", json={"content": text}).json()["created"] is False
    listed = {(m["ruleset_id"], m["ruleset_version"]) for m in client.get("/rulebooks").json()}
    assert ("house-fate", "1.0.0") in listed
    assert client.get("/rulebooks/house-fate?version=1.0.0").json()["title"] == "House Fate"
    created = client.post(
        "/worlds/house", json={"ruleset_id": "house-fate", "ruleset_version": "1.0.0"}
    )
    assert created.status_code == 201, created.text
    turn = client.post(
        "/worlds/house/events",
        json={
            "event_id": "t1",
            "kind": "overcome",
            "actor_id": "player",
            "payload": {"skill": "investigate", "difficulty": 2},
        },
    )
    assert turn.status_code == 200, turn.text

    repo2, client2 = client_for(tmp_path)
    assert client2.get("/worlds/house/ruleset").json()["manifest"]["ruleset_id"] == "house-fate"
    assert client2.get("/worlds/house/replay").status_code == 200
    bad = client.post("/rulebooks", json={"content": text.replace("House Fate", "Other")})
    assert bad.status_code == 409
    assert client.post("/rulebooks", json={"content": "nope"}).status_code == 422


def test_store_refuses_any_registered_identity_and_never_publishes_an_unbuildable_file(
    tmp_path,
):
    repo, client = client_for(tmp_path)
    older = json.loads(SRD_TEXT)
    older["version"] = "0.3.0"
    assert client.post("/rulebooks", json={"content": json.dumps(older)}).status_code == 409
    broken = json.loads(FATE_TEXT)
    broken["id"], broken["version"] = "broken", "0.1.0"
    broken["actions"]["overcome"]["input"]["model_config"] = {"type": "boolean"}
    response = client.post("/rulebooks", json={"content": json.dumps(broken)})
    assert response.status_code == 422, response.text
    assert "model_config" in response.json()["detail"]
    assert not (tmp_path / "rulebooks").exists() or not list((tmp_path / "rulebooks").iterdir())
    SQLiteWorldRepository(tmp_path / "worlds.sqlite3")  # still starts


def test_store_hashes_submitted_bytes_and_publishes_without_overwriting(tmp_path):
    store = RulebookStore(tmp_path / "rulebooks")
    crlf = FATE_TEXT.replace("\n", "\r\n")
    spec, created = store.register(crlf)
    assert created
    assert store.register(crlf)[1] is False
    assert store.path_for(spec.id, spec.version).read_bytes() == crlf.encode("utf-8")
    with pytest.raises(RepositoryConflict):
        store.register(FATE_TEXT)
    assert not list((tmp_path / "rulebooks").glob("*.tmp*"))


def test_second_repository_sees_a_rulebook_imported_through_the_first(tmp_path):
    first = SQLiteWorldRepository(tmp_path / "worlds.sqlite3")
    second = SQLiteWorldRepository(tmp_path / "worlds.sqlite3")
    custom = json.loads(FATE_TEXT)
    custom["id"], custom["version"] = "late-fate", "1.0.0"
    first.register_rulebook(json.dumps(custom))
    initial = first.registry.get("late-fate", "1.0.0").example_world("late")
    first.create_world(initial, ruleset_id="late-fate", ruleset_version="1.0.0")
    assert second.ruleset_for_world("late").ruleset_id == "late-fate"
    assert second.get_state("late").world_id == "late"
    assert second.registry.get("late-fate", "1.0.0").manifest.ruleset_version == "1.0.0"
