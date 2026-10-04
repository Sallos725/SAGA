import asyncio
import base64
import json
import logging
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from google.oauth2 import service_account
from pydantic import SecretStr
from test_risu_api import request, setup

SECRET = "SYNTHETIC_PRIVATE_KEY_DO_NOT_LEAK"
TOKEN = "SYNTHETIC_ACCESS_TOKEN_DO_NOT_LEAK"
ACCOUNT = {
    "type": "service_account",
    "project_id": "saga-test-project",
    "client_email": "narrator@saga-test-project.iam.gserviceaccount.com",
    "private_key": SECRET,
    "token_uri": "https://oauth2.googleapis.com/token",
}


def vertex_body():
    body = request()
    body["narration"] = {
        "schema_version": 1,
        "provider": "vertex",
        "context": ["system: A misty arena.", "user: attack opponent"],
    }
    return body


def vertex_post(client, body=None, account=None):
    encoded = base64.b64encode(
        json.dumps(ACCOUNT if account is None else account).encode()
    ).decode()
    return client.post(
        "/v1/chat/completions",
        json=body or vertex_body(),
        headers={"x-saga-vertex-secret": encoded},
    )


@pytest.fixture
def fake_credentials(monkeypatch):
    seen = []

    def create(info, *, scopes):
        seen.append((dict(info), list(scopes)))
        credentials = SimpleNamespace(token=TOKEN)
        credentials.refresh = lambda transport: None
        return credentials

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", create)
    return seen


def install_vertex(monkeypatch, handler):
    from saga_trpg.adapters.narrator.vertex import VertexNarrator
    from saga_trpg.adapters.risu import openai_proxy

    monkeypatch.setattr(
        openai_proxy,
        "VertexNarrator",
        lambda secret: VertexNarrator(secret, transport=httpx.MockTransport(handler)),
    )


def test_one_json_input_derives_fixed_endpoint_after_commit_and_replays(
    tmp_path, monkeypatch, fake_credentials
):
    repo, dice, intent, client = setup(tmp_path)
    seen = []

    def remote(req):
        body = json.loads(req.content)
        committed = json.loads(body["contents"][-1]["parts"][0]["text"])
        event_id = committed["event_id"]
        saved = repo.get_event(committed["world_id"], event_id, branch_id=event_id)
        assert saved is not None
        assert committed["state"] == saved.result.state.model_dump(mode="json")
        assert str(req.url) == (
            "https://aiplatform.googleapis.com/v1/projects/saga-test-project/locations/"
            "global/publishers/google/models/gemini-3.8-flash:generateContent"
        )
        assert req.headers["authorization"] == "Bearer " + TOKEN
        assert body["generationConfig"] == {
            "thinkingConfig": {"thinkingLevel": "LOW", "includeThoughts": False},
            "maxOutputTokens": 2048,
        }
        assert SECRET not in req.content.decode() and TOKEN not in req.content.decode()
        assert "tools" not in body
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "hidden reasoning", "thought": True},
                                {"text": f"Narration {len(seen)}"},
                            ]
                        }
                    }
                ]
            },
        )

    install_vertex(monkeypatch, remote)
    first = vertex_post(client)
    assert first.status_code == 200
    saved = first.json()["saga"]
    entries = repo.entries(saved["world_id"], branch_id=saved["branch_id"])
    calls = dice.calls, intent.calls
    assert calls[0] > 0 and calls[1] == 1
    for _ in range(10):
        replay = vertex_post(client)
        assert replay.status_code == 200
        assert replay.json()["saga"] == {**saved, "replayed": True}
        assert "hidden reasoning" not in replay.text
        assert (dice.calls, intent.calls) == calls
    assert len(seen) == 11
    assert (
        len(
            {
                first.json()["choices"][0]["message"]["content"],
                replay.json()["choices"][0]["message"]["content"],
            }
        )
        == 2
    )
    assert repo.entries(saved["world_id"], branch_id=saved["branch_id"]) == entries
    assert fake_credentials[0][0]["token_uri"] == "https://oauth2.googleapis.com/token"
    assert fake_credentials[0][1] == ["https://www.googleapis.com/auth/cloud-platform"]
    assert SECRET.encode() not in (tmp_path / "risu.sqlite3").read_bytes()
    assert TOKEN.encode() not in (tmp_path / "risu.sqlite3").read_bytes()
    _, restarted_dice, restarted_intent, restarted = setup(tmp_path)
    assert vertex_post(restarted).status_code == 200
    assert restarted_dice.calls == restarted_intent.calls == 0


@pytest.mark.parametrize(
    "account",
    [
        {},
        {**ACCOUNT, "type": "authorized_user"},
        {**ACCOUNT, "project_id": "../SECRET"},
        {**ACCOUNT, "client_email": "not-an-email"},
        {**ACCOUNT, "private_key": ""},
        {**ACCOUNT, "token_uri": "https://attacker.invalid/token"},
    ],
)
def test_invalid_account_is_generic_and_never_commits(tmp_path, account, fake_credentials):
    repo, dice, intent, client = setup(tmp_path)
    body = vertex_body()
    response = vertex_post(client, body, account)
    assert response.status_code == 422
    assert SECRET not in response.text and "attacker.invalid" not in response.text
    assert dice.calls == intent.calls == 0
    with sqlite3.connect(tmp_path / "risu.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM worlds").fetchone()[0] == 0


def test_invalid_private_key_is_checked_before_commit(tmp_path):
    _, dice, intent, client = setup(tmp_path)
    response = vertex_post(client)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert "JSON" in response.text
    assert dice.calls == intent.calls == 0


@pytest.mark.parametrize(
    "damage", ["wrong-role", "extra-field", "wrong-secret-type", "invalid-json-body"]
)
def test_fastapi_validation_never_echoes_secret_input(tmp_path, damage):
    _, dice, _, client = setup(tmp_path)
    body = vertex_body()
    if damage == "wrong-role":
        body["messages"][0]["role"] = SECRET
    elif damage == "extra-field":
        body["private_key"] = SECRET
    elif damage == "wrong-secret-type":
        body["narration"]["context"] = {"private_key": SECRET}
    else:
        response = client.post(
            "/v1/chat/completions",
            content='{"private_key":"' + SECRET + '",',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422 and SECRET not in response.text
        return
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 422 and SECRET not in response.text
    assert dice.calls == 0


@pytest.mark.parametrize("failure", ["http", "empty", "malformed", "timeout", "auth"])
def test_auth_model_failures_preserve_commit_and_do_not_leak(
    tmp_path, monkeypatch, caplog, fake_credentials, failure
):
    from saga_trpg.adapters.narrator.vertex import VertexNarrator

    _, dice, intent, client = setup(tmp_path)

    def remote(req):
        if failure == "timeout":
            raise httpx.ReadTimeout(SECRET)
        if failure == "http":
            return httpx.Response(403, json={"error": {"message": SECRET}})
        if failure == "malformed":
            return httpx.Response(200, text=SECRET)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": " "}]}}]})

    if failure == "auth":

        def auth(_):
            raise RuntimeError(SECRET)

        monkeypatch.setattr(VertexNarrator, "_access_token", auth)
    install_vertex(monkeypatch, remote)
    with caplog.at_level(logging.WARNING):
        response = vertex_post(client)
    assert response.status_code == 200
    assert response.json()["saga"]["seq"] == 1
    assert dice.calls > 0 and intent.calls == 1
    assert "서술" in response.json()["choices"][0]["message"]["content"]
    assert SECRET not in response.text and TOKEN not in response.text
    assert SECRET not in caplog.text and TOKEN not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_total_narration_timeout_is_bounded(monkeypatch, fake_credentials):
    from saga_trpg.adapters.narrator import vertex
    from saga_trpg.domain.events import GameEvent
    from saga_trpg.domain.models import RuleResult, WorldState
    from saga_trpg.ports.narrator import NarrationRequest

    monkeypatch.setattr(vertex, "NARRATION_TIMEOUT_SECONDS", 0.01)

    async def remote(req):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    narrator = vertex.VertexNarrator(
        SecretStr(json.dumps(ACCOUNT)), transport=httpx.MockTransport(remote)
    )
    request_ = NarrationRequest(
        world_id="w",
        event=GameEvent(event_id="e", kind="x", actor_id="player"),
        result=RuleResult(state=WorldState(world_id="w")),
    )
    with pytest.raises(TimeoutError):
        await narrator.render(request_)


def test_fallback_without_credentials_does_not_construct_vertex(tmp_path, monkeypatch):
    from saga_trpg.adapters.risu import openai_proxy

    def forbidden(_):
        raise AssertionError("Vertex must stay disabled")

    monkeypatch.setattr(openai_proxy, "VertexNarrator", forbidden, raising=False)
    _, _, _, client = setup(tmp_path)
    assert client.post("/v1/chat/completions", json=request()).status_code == 200


@pytest.mark.parametrize("encoded", ["", "not-base64!", "////", "e25vdCBqc29ufQ=="])
def test_malformed_secret_header_never_commits_or_echoes(tmp_path, encoded):
    _, dice, intent, client = setup(tmp_path)
    response = client.post(
        "/v1/chat/completions", json=vertex_body(), headers={"x-saga-vertex-secret": encoded}
    )
    assert response.status_code == 422
    assert "JSON" in response.text
    if encoded:
        assert encoded not in response.text
    assert dice.calls == intent.calls == 0


def test_narration_requires_header_and_config_together(tmp_path):
    _, dice, intent, client = setup(tmp_path)
    assert client.post("/v1/chat/completions", json=vertex_body()).status_code == 422
    assert vertex_post(client, request()).status_code == 422
    assert dice.calls == intent.calls == 0


@pytest.mark.parametrize("domain", ["saga-test-project.iam", "developer", "appspot"])
def test_standard_service_account_email_domains_are_accepted(domain, fake_credentials):
    from saga_trpg.adapters.narrator.vertex import VertexNarrator

    account = {**ACCOUNT, "client_email": f"123456@{domain}.gserviceaccount.com"}
    VertexNarrator(SecretStr(json.dumps(account)))
    assert len(fake_credentials) == 1


def test_real_google_sdk_signing_refresh_and_vertex_with_in_memory_test_key(
    tmp_path, monkeypatch, caplog
):
    import requests
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    account = {**ACCOUNT, "private_key": pem, "client_email": "12345@developer.gserviceaccount.com"}
    token_calls = []

    def token_request(session, method, url, *, data, headers, timeout, **kwargs):
        from urllib.parse import parse_qs

        assert method == "POST" and url == "https://oauth2.googleapis.com/token"
        assert timeout == 5 and session.max_redirects == 0
        assertion = parse_qs(data.decode())["assertion"][0]
        header, claims, signature = assertion.split(".")
        key.public_key().verify(
            base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
            (header + "." + claims).encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        payload = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
        assert payload["iss"] == account["client_email"]
        assert payload["aud"] == url
        assert payload["scope"] == "https://www.googleapis.com/auth/cloud-platform"
        token_calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response.headers["content-type"] = "application/json"
        response._content = json.dumps(
            {"access_token": TOKEN, "expires_in": 3600, "token_type": "Bearer"}
        ).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", token_request)
    repo, dice, intent, client = setup(tmp_path)

    def remote(req):
        assert req.headers["authorization"] == "Bearer " + TOKEN
        body = json.loads(req.content)
        packet = json.loads(body["contents"][-1]["parts"][0]["text"])
        assert repo.get_event(packet["world_id"], packet["event_id"], branch_id=packet["event_id"])
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "SDK boundary verified"}]}}]}
        )

    install_vertex(monkeypatch, remote)
    with caplog.at_level(logging.DEBUG):
        response = vertex_post(client, account=account)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "SDK boundary verified"
    assert len(token_calls) == 1 and dice.calls > 0 and intent.calls == 1
    for text in (response.text, caplog.text, repr([r.__dict__ for r in caplog.records])):
        assert TOKEN not in text and pem not in text
    for file in tmp_path.iterdir():
        if file.is_file():
            assert TOKEN.encode() not in file.read_bytes()
            assert pem.encode() not in file.read_bytes()
