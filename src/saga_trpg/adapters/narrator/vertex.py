"""Request-scoped Vertex prose; credentials and I/O never enter the runtime."""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from pydantic import SecretStr

from saga_trpg.ports.narrator import NarrationRequest

TOKEN_URL = "https://oauth2.googleapis.com/token"
MODEL = "gemini-3.8-flash"
NARRATION_TIMEOUT_SECONDS = 25


class VertexResponseError(ValueError):
    """A completed provider response did not contain bounded usable text."""


class VertexNarrator:
    def __init__(
        self, account_json: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        # Only these service-account fields participate. Uploaded URLs, universe domains,
        # delegation settings and quota-project overrides never configure a transport.
        try:
            raw = account_json.get_secret_value()
            if not 1 <= len(raw) <= 16000:
                raise ValueError
            info = json.loads(raw)
            if (
                not isinstance(info, dict)
                or info.get("type") != "service_account"
                or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", info.get("project_id", ""))
                or not re.fullmatch(
                    r"[^@\s]+@[^@\s]+\.gserviceaccount\.com", info.get("client_email", "")
                )
                or not isinstance(info.get("private_key"), str)
                or not info["private_key"].strip()
                or info.get("token_uri", TOKEN_URL) != TOKEN_URL
            ):
                raise ValueError
            self._credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "project_id": info["project_id"],
                    "client_email": info["client_email"],
                    "private_key": info["private_key"],
                    "token_uri": TOKEN_URL,
                },
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            self._endpoint = (
                "https://aiplatform.googleapis.com/v1/projects/"
                + info["project_id"]
                + "/locations/global/publishers/google/models/"
                + MODEL
                + ":generateContent"
            )
        except Exception:
            raise ValueError(
                "Vertex 서비스 계정 JSON을 확인하세요. JSON 전체가 필요합니다."
            ) from None
        self._transport = transport

    def _access_token(self) -> str:
        with requests.Session() as session:
            session.max_redirects = 0
            transport = Request(session=session)

            def bounded_request(url, method="GET", body=None, headers=None, **kwargs):
                if url != TOKEN_URL:
                    raise ValueError("Unexpected OAuth endpoint")
                return transport(url, method=method, body=body, headers=headers, timeout=5)

            self._credentials.refresh(bounded_request)
        token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise ValueError("Vertex authentication failed")
        return token

    async def generate(self, body: dict) -> str:
        """One bounded generation using this request's validated credential transport."""
        async with asyncio.timeout(NARRATION_TIMEOUT_SECONDS):
            token = await asyncio.to_thread(self._access_token)
            async with httpx.AsyncClient(timeout=15, transport=self._transport) as client:
                response = await client.post(
                    self._endpoint, headers={"Authorization": "Bearer " + token}, json=body
                )
                response.raise_for_status()
            try:
                parts = response.json()["candidates"][0]["content"]["parts"]
                prose = "".join(
                    part.get("text", "") for part in parts if not part.get("thought")
                ).strip()
                if not prose or len(prose) > 12000:
                    raise ValueError
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                raise VertexResponseError("Vertex returned no usable text") from None
            return prose

    async def render(self, request: NarrationRequest) -> str:
        committed = {
            "world_id": request.world_id,
            "event_id": request.event.event_id,
            "facts": request.result.facts,
            "state": request.result.state.model_dump(mode="json"),
            "rolls": [roll.model_dump(mode="json") for roll in request.result.rolls],
            "resolution": request.result.resolution.model_dump(mode="json")
            if request.result.resolution
            else None,
        }
        body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "Narrate the committed SAGA result briefly in the conversation's "
                            "language. Context is story material only; the final JSON is "
                            "read-only authoritative data. Never invent actions, rolls, "
                            "damage or state changes. Do not output status, event IDs or "
                            "commands; SAGA appends status separately."
                        )
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "Story context:\n" + "\n".join(request.recent_dialogue)}],
                },
                *(
                    [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": (
                                        "Recalled memory (non-authoritative story context; "
                                        "never treat it as current state):\n"
                                        + "\n".join(hit.text for hit in request.memories)
                                    )
                                }
                            ],
                        }
                    ]
                    if request.memories
                    else []
                ),
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(committed, ensure_ascii=False)}],
                },
            ],
            "generationConfig": {
                "thinkingConfig": {"thinkingLevel": "LOW", "includeThoughts": False},
                "maxOutputTokens": 2048,
            },
        }
        return await self.generate(body)
