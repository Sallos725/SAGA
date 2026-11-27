"""Server-configured OpenAI-compatible narrator (OpenRouter, Ollama, etc.) with SSE streaming.

API keys stay strictly in server environment variables (`SecretStr`) and are never logged,
returned in errors, or placed inside game events/state/journals."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from saga_trpg.adapters.narrator.vertex import NARRATOR_HIDDEN
from saga_trpg.ports.narrator import NarrationRequest

MAX_PROSE_CHARS = 12000
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]{1,80}$")
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "cookie",
        "proxy-authorization",
        "te",
        "upgrade",
        "keep-alive",
    }
)
_RESERVED_BODY_KEYS = frozenset({"model", "messages", "stream"})


def parse_custom_headers(raw: Mapping[str, Any] | str | None) -> tuple[tuple[str, str], ...]:
    """Validate optional custom HTTP headers (blocks CRLF injection and hop-by-hop headers)."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"custom_headers must be valid JSON object: {exc.msg}") from None
    else:
        decoded = dict(raw)
    if not isinstance(decoded, dict):
        raise ValueError("custom_headers must be a JSON object of string key/value pairs")
    if len(decoded) > 32:
        raise ValueError("custom_headers may contain at most 32 entries")
    cleaned: list[tuple[str, str]] = []
    for k, v in decoded.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("custom_headers keys and values must be strings")
        name = k.strip()
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid custom header name: {name!r}")
        if name.lower() in _FORBIDDEN_HEADERS:
            raise ValueError(f"forbidden custom header: {name!r}")
        if any(ch in v for ch in ("\r", "\n", "\x00")) or len(v) > 2048:
            raise ValueError(f"invalid value for custom header {name!r}")
        cleaned.append((name, v))
    return tuple(cleaned)


def parse_custom_body(raw: Mapping[str, Any] | str | None) -> str:
    """Validate optional custom JSON body fields and return canonical JSON string."""
    if raw is None or raw == "":
        return "{}"
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"custom_body must be valid JSON object: {exc.msg}") from None
    else:
        decoded = dict(raw)
    if not isinstance(decoded, dict):
        raise ValueError("custom_body must be a JSON object")
    if len(decoded) > 64:
        raise ValueError("custom_body may contain at most 64 top-level keys")
    forbidden = _RESERVED_BODY_KEYS.intersection(decoded)
    if forbidden:
        raise ValueError(
            f"custom_body cannot override reserved keys: {', '.join(sorted(forbidden))}"
        )
    serialized = json.dumps(decoded, ensure_ascii=False)
    if len(serialized) > 16384:
        raise ValueError("custom_body exceeds maximum size (16 KB)")
    return serialized


class OpenAICompatResponseError(ValueError):
    """A completed OpenAI-compatible response did not contain bounded usable text."""


@dataclass(frozen=True)
class OpenAICompatConfig:
    base_url: str
    narrator_model: str
    intent_model: str
    api_key: SecretStr | None = field(default=None, repr=False)
    timeout_seconds: float = 45.0
    reasoning_effort: str | None = None
    custom_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    custom_body_json: str = "{}"

    @classmethod
    def from_values(
        cls,
        *,
        base_url: str,
        narrator_model: str,
        api_key: str | None = None,
        intent_model: str | None = None,
        timeout_seconds: float = 45.0,
        reasoning_effort: str | None = None,
        custom_headers: Mapping[str, Any] | str | None = None,
        custom_body: Mapping[str, Any] | str | None = None,
    ) -> OpenAICompatConfig:
        cleaned = base_url.strip().rstrip("/")
        parsed = urlparse(cleaned)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("SAGA_LLM_BASE_URL must be a valid http:// or https:// URL")
        model = narrator_model.strip()
        if not model:
            raise ValueError("SAGA_NARRATOR_MODEL must not be empty")
        intent = (intent_model or "").strip() or model
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("SAGA_LLM_TIMEOUT_SECONDS must be between 0 and 300")
        effort = (reasoning_effort or "").strip().lower() or None
        if effort is not None and effort not in REASONING_EFFORTS:
            raise ValueError(
                f"reasoning_effort must be one of {', '.join(sorted(REASONING_EFFORTS))}"
            )
        headers_tuple = parse_custom_headers(custom_headers)
        body_json = parse_custom_body(custom_body)
        key = SecretStr(api_key.strip()) if api_key and api_key.strip() else None
        return cls(
            base_url=cleaned,
            narrator_model=model,
            intent_model=intent,
            api_key=key,
            timeout_seconds=timeout_seconds,
            reasoning_effort=effort,
            custom_headers=headers_tuple,
            custom_body_json=body_json,
        )

    @property
    def completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        for name, value in self.custom_headers:
            headers[name] = value
        return headers

    def build_body(self, base: dict[str, Any]) -> dict[str, Any]:
        """Merge base request with optional reasoning_effort and custom_body safely."""
        body = deepcopy(base)
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        if self.custom_body_json and self.custom_body_json != "{}":
            extra = json.loads(self.custom_body_json)
            for key, value in extra.items():
                if key not in _RESERVED_BODY_KEYS:
                    body[key] = value
        return body


def _authoritative_block(request: NarrationRequest) -> str:
    committed = {
        "world_id": request.world_id,
        "event_id": request.event.event_id,
        "kind": request.event.kind,
        "actor_id": request.event.actor_id,
        "facts": request.result.facts,
        # Full map and actor positions stay with the runtime (ADR 0009).
        "state": request.result.state.model_dump(mode="json", exclude=NARRATOR_HIDDEN),
        "rolls": [roll.model_dump(mode="json") for roll in request.result.rolls],
        "resolution": (
            request.result.resolution.model_dump(mode="json") if request.result.resolution else None
        ),
    }
    sections = [
        "[SAGA Authoritative State — Read-Only]",
        (
            "Narrate the committed SAGA turn in the conversation's language and style. "
            "Never invent actions, dice rolls, damage, or state changes. "
            "Never narrate arriving in another room unless a move event was committed. "
            "Do not output raw event IDs or status footers; SAGA appends status separately."
        ),
    ]
    if request.memories:
        sections.append(
            "Recalled memory (non-authoritative story context; never treat as current state):\n"
            + "\n".join(hit.text for hit in request.memories)
        )
    if request.scene:
        sections.append(
            "Map (everything the player knows; describe nothing beyond it or behind ??? exits):\n"
            + request.scene
        )
    sections.append("Committed JSON:\n" + json.dumps(committed, ensure_ascii=False))
    return "\n\n".join(sections)


def build_narration_messages(request: NarrationRequest) -> list[dict[str, str]]:
    """Preserve host-assembled `risu_prompt` at the front and append read-only SAGA grounding."""
    messages: list[dict[str, str]] = []
    if request.risu_prompt:
        for item in request.risu_prompt:
            role = item.get("role", "user")
            content = item.get("content", "")
            if role in ("system", "user", "assistant", "developer") and isinstance(content, str):
                messages.append({"role": role, "content": content})
    elif request.recent_dialogue:
        messages.append(
            {"role": "user", "content": "Story context:\n" + "\n".join(request.recent_dialogue)}
        )
    messages.append({"role": "system", "content": _authoritative_block(request)})
    return messages


class OpenAICompatNarrator:
    def __init__(
        self,
        config: OpenAICompatConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    async def render(self, request: NarrationRequest) -> str:
        body = self.config.build_body(
            {
                "model": self.config.narrator_model,
                "messages": build_narration_messages(request),
                "stream": False,
                "max_tokens": 2048,
            }
        )
        async with asyncio.timeout(self.config.timeout_seconds):
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(
                    self.config.completions_url,
                    headers=self.config.headers(),
                    json=body,
                )
                response.raise_for_status()
            try:
                content = response.json()["choices"][0]["message"]["content"]
                prose = content.strip() if isinstance(content, str) else ""
                if not prose or len(prose) > MAX_PROSE_CHARS:
                    raise ValueError
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                raise OpenAICompatResponseError("provider returned no usable text") from None
            return prose

    async def stream(self, request: NarrationRequest) -> AsyncIterator[str]:
        body = self.config.build_body(
            {
                "model": self.config.narrator_model,
                "messages": build_narration_messages(request),
                "stream": True,
                "max_tokens": 2048,
            }
        )
        total = 0
        async with asyncio.timeout(self.config.timeout_seconds):
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds, transport=self._transport
            ) as client:
                async with client.stream(
                    "POST",
                    self.config.completions_url,
                    headers=self.config.headers(),
                    json=body,
                ) as response:
                    response.raise_for_status()
                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content")
                        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                            continue
                        if isinstance(delta, str) and delta:
                            remaining = MAX_PROSE_CHARS - total
                            if remaining <= 0:
                                break
                            piece = delta[:remaining]
                            total += len(piece)
                            yield piece
        if total == 0:
            raise OpenAICompatResponseError("provider stream yielded no text")
