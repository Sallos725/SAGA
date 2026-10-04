import sqlite3

from fastapi.testclient import TestClient

from nmos_trpg.api.app import create_app


def test_world_selects_one_ruleset_and_keeps_it_on_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        catalog = client.get("/rulesets")
        assert catalog.status_code == 200
        licensed = {item["ruleset_id"]: item for item in catalog.json() if item["license_id"]}
        assert set(licensed) == {"srd-5.2.1", "fate-condensed"}
        assert licensed["srd-5.2.1"]["license_id"] == "CC-BY-4.0"
        assert licensed["fate-condensed"]["license_id"] == "CC-BY-3.0"
        for world_id, ruleset in (("fantasy", "srd-5.2.1"), ("mystery", "fate-condensed")):
            created = client.post(
                f"/worlds/{world_id}",
                json={
                    "ruleset_id": ruleset,
                    "ruleset_version": "0.1.0",
                },
            )
            assert created.status_code == 201
            context = client.get(f"/worlds/{world_id}/ruleset").json()
            assert context["manifest"]["ruleset_id"] == ruleset
            assert context["manifest"]["attribution"]
            assert context["common_bot_description"]
        fantasy = {
            "event_id": "turn-1",
            "actor_id": "player",
            "kind": "attack",
            "payload": {"target_id": "opponent"},
        }
        mystery = {
            "event_id": "turn-1",
            "actor_id": "player",
            "kind": "create_advantage",
            "payload": {"skill": "investigate", "difficulty": -10, "aspect_name": "Mapped route"},
        }
        first = client.post("/worlds/fantasy/events", json=fantasy)
        second = client.post("/worlds/mystery/events", json=mystery)
        assert first.status_code == second.status_code == 200
        assert first.json()["resolution"]["outcome"] in {"hit", "critical_hit", "miss"}
        assert second.json()["resolution"]["outcome"] == "success_with_style"
        assert second.json()["state"]["flags"]["fate_aspects"]["turn-1"]["free_invokes"] == 2
        assert (
            client.post(
                "/worlds/mystery/events", json={**fantasy, "event_id": "invalid"}
            ).status_code
            == 422
        )
        assert len(client.get("/worlds/mystery/events").json()) == 1
        assert (
            client.post(
                "/worlds/fantasy", json={"ruleset_id": "fate-condensed", "ruleset_version": "0.1.0"}
            ).status_code
            == 409
        )

    with TestClient(create_app()) as client:
        for world_id, action, expected in (
            ("fantasy", fantasy, first.json()),
            ("mystery", mystery, second.json()),
        ):
            repeated = client.post(f"/worlds/{world_id}/events", json=action)
            assert repeated.status_code == 200
            assert repeated.json() == {**expected, "replayed": True}
            assert client.get(f"/worlds/{world_id}/replay").json() == expected["state"]
            assert (
                client.post(
                    f"/worlds/{world_id}/branches",
                    json={"branch_id": "alternate", "at_event_id": "turn-1"},
                ).status_code
                == 201
            )
            assert (
                client.get(f"/worlds/{world_id}/state?branch_id=alternate").json()
                == expected["state"]
            )
        assert (
            client.get("/worlds/mystery/ruleset").json()["manifest"]["ruleset_id"]
            == "fate-condensed"
        )


def test_unknown_ruleset_version_is_rejected_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        for choice in (
            {"ruleset_id": "unknown", "ruleset_version": "0.1.0"},
            {"ruleset_id": "srd-5.2.1", "ruleset_version": "999"},
        ):
            assert client.post("/worlds/bad", json=choice).status_code == 422
        assert client.get("/worlds/bad/ruleset").status_code == 404
        assert (
            client.post(
                "/worlds/fantasy", json={"ruleset_id": "srd-5.2.1", "ruleset_version": "0.1.0"}
            ).status_code
            == 201
        )
        with sqlite3.connect(tmp_path / "worlds.sqlite3") as connection:
            connection.execute(
                "UPDATE worlds SET ruleset_version = '999' WHERE world_id = 'fantasy'"
            )
        assert client.get("/worlds/fantasy/state").status_code == 503
