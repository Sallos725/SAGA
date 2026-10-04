import sqlite3

from fastapi.testclient import TestClient

from saga_trpg.api.app import create_app

ATTACK = {
    "kind": "attack",
    "actor_id": "player",
    "idempotency_key": "turn-1",
    "payload": {"target_id": "goblin", "attack_bonus": 20, "target_ac": 10, "damage": "1d6+2"},
}


def test_api_restart_retry_replay_and_branch_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        first = client.post("/worlds/demo/events", json=ATTACK)
        assert first.status_code == 200
        event_id = first.json()["event_id"]
        client.post("/worlds/demo/events", json={"kind": "time_advance", "actor_id": "player"})
        original_state = client.get("/worlds/demo/state").json()
        original_history = client.get("/worlds/demo/events").json()

    with TestClient(create_app()) as restarted:
        assert restarted.get("/worlds/demo/state").json() == original_state
        for _ in range(10):
            response = restarted.post("/worlds/demo/events", json=ATTACK)
            assert response.status_code == 200
            assert response.json() == {**first.json(), "replayed": True}
        assert restarted.get("/worlds/demo/events").json() == original_history
        assert restarted.get("/worlds/demo/state").json() == original_state
        assert restarted.get("/worlds/demo/replay").json() == original_state
        assert (
            restarted.get("/worlds/demo/replay", params={"at_event_id": event_id}).json()
            == first.json()["state"]
        )
        branch = restarted.post(
            "/worlds/demo/branches", json={"branch_id": "alternate", "at_event_id": event_id}
        )
        assert branch.status_code == 201
        assert branch.json()["fork_seq"] == 1
        assert branch.json()["parent_branch_id"] == "main"
        assert (
            restarted.get("/worlds/demo/state", params={"branch_id": "alternate"}).json()
            == first.json()["state"]
        )
        branched = restarted.post(
            "/worlds/demo/events?branch_id=alternate", json={**ATTACK, "idempotency_key": "turn-2"}
        )
        assert branched.status_code == 200
        assert restarted.get("/worlds/demo/state").json() == original_state
        assert restarted.get("/worlds/demo/events").json() == original_history
        assert len(restarted.get("/worlds/demo/branches").json()) == 2

    with TestClient(create_app()) as restarted:
        assert (
            restarted.get("/worlds/demo/state?branch_id=alternate").json()
            == branched.json()["state"]
        )


def test_api_conflicts_and_invalid_requests_do_not_mutate(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        assert client.post("/worlds/demo/events", json=ATTACK).status_code == 200
        before = client.get("/worlds/demo/events").json()
        conflict = {"kind": "time_advance", "actor_id": "player", "idempotency_key": "turn-1"}
        assert client.post("/worlds/demo/events", json=conflict).status_code == 409
        assert (
            client.post("/worlds/demo/events", json={**ATTACK, "actor_id": ""}).status_code == 422
        )
        assert client.post("/worlds/demo/events", json={**ATTACK, "extra": 1}).status_code == 422
        assert (
            client.post(
                "/worlds/demo/events", json={"kind": "attack", "actor_id": "player"}
            ).status_code
            == 422
        )
        assert client.get("/worlds/demo/state?branch_id=missing").status_code == 404
        assert client.get("/worlds/demo/events/missing").status_code == 404
        assert (
            client.post("/worlds/demo/branches", json={"branch_id": "missing-marker"}).status_code
            == 422
        )
        genesis = {"branch_id": "genesis", "at_event_id": None}
        assert client.post("/worlds/demo/branches", json=genesis).status_code == 201
        assert client.post("/worlds/demo/branches", json=genesis).status_code == 409
        assert client.get("/worlds/demo/state?branch_id=genesis").json()["seq"] == 0
        assert client.get("/worlds/demo/events").json() == before


def test_api_storage_failure_is_reported_without_committing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        before = client.get("/worlds/demo/state").json()
        with sqlite3.connect(tmp_path / "worlds.sqlite3") as connection:
            connection.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON entries
                                  BEGIN SELECT RAISE(ABORT, 'test disk failure'); END""")
        assert client.post("/worlds/demo/events", json=ATTACK).status_code == 503
        assert client.get("/worlds/demo/state").json() == before
        assert client.get("/worlds/demo/events").json() == []
