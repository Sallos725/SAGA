"""NMOS sidecar 0.3.0 binding for `MemoryPort`.

Read from the running sidecar's OpenAPI on 2026-10-06 (`docs/NMOS_SIDECAR.md`). The sidecar
models a *conversation* whose message manifest the host reconciles (`POST /v1/sync/reconcile`
or `POST /v1/sync/bodies` with `then_reconcile`) and which it queries with
`POST /v1/retrieve`. SAGA maps:

- one world branch → one conversation (`chat_id = "<world_id>/<branch_id>"`, `host = "saga"`);
- one committed event → one message whose `host_logical_id` is the event id and whose
  `revision_hash` is the sha256 of its committed facts text;
- `retrieve(query)` → the sidecar's packet text as a single hit.

The adapter keeps no state: the caller passes the branch's full committed history in
`MemoryWrite.metadata["history"]` (built from the journal), so a sidecar that lost its data
can ask for any body and get it. Every failure is soft: a write logs and returns, a read logs
and returns no hits. Exact game facts stay in SAGA; the sidecar only ever sees prose facts.
Actor-scoped (epistemic) retrieval is not offered by this sidecar version and is ignored.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import httpx

from saga_trpg.ports.memory import MemoryHit, MemoryWrite

logger = logging.getLogger(__name__)

HOST = "saga"
ROLE = "char"
NAME = "SAGA"
_TOKENS_PER_HIT = 120


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def history_entry(event_id: str, text: str) -> dict[str, str]:
    """One committed event as the caller should list it in `metadata["history"]`."""
    return {"event_id": event_id, "text": text, "hash": sha256_text(text)}


class NMOSAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(timeout_seconds)
        )
        # Per-conversation serialization within this process, and the longest manifest sent
        # so far, so a delayed request carrying an older, shorter history cannot roll the
        # sidecar's active conversation backwards. Other processes are not coordinated.
        self._locks: dict[str, asyncio.Lock] = {}
        self._manifest_length: dict[str, int] = {}

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = await self._client.post(path, json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("NMOS %s failed (fail-soft): %s", path, exc)
            return None

    async def health(self) -> dict[str, Any] | None:
        try:
            response = await self._client.get("/v1/health", headers=self._headers())
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("NMOS health failed: %s", exc)
            return None

    # -- MemoryPort ----------------------------------------------------------------------------

    async def remember(self, item: MemoryWrite) -> None:
        branch_id = str(item.metadata.get("branch_id") or "main")
        chat_id = f"{item.world_id}/{branch_id}"
        history = item.metadata.get("history")
        if not isinstance(history, list) or not history:
            history = [history_entry(item.event_id, item.text)]
        entries = [
            entry
            for entry in history
            if isinstance(entry, dict) and "event_id" in entry and "text" in entry
        ]
        by_id = {
            str(entry["event_id"]): {
                "text": str(entry["text"]),
                "hash": str(entry.get("hash") or sha256_text(str(entry["text"]))),
            }
            for entry in entries
        }
        manifest = [
            {
                "host_logical_id": event_id,
                "revision_hash": body["hash"],
                "role": ROLE,
                "name": NAME,
                "swipe_count": 1,
                "special_comments": [],
            }
            for event_id, body in by_id.items()
        ]
        current = by_id.get(item.event_id) or {
            "text": item.text,
            "hash": sha256_text(item.text),
        }
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            payload: dict[str, Any] = {
                "host": HOST,
                "chat_id": chat_id,
                "bodies": [self._body(item.event_id, current, item)],
            }
            stale = len(manifest) < self._manifest_length.get(chat_id, 0)
            if not stale:
                payload["then_reconcile"] = {
                    "host": HOST,
                    "chat_id": chat_id,
                    "hash_version": 1,
                    "messages": manifest,
                }
            response = await self._post("/v1/sync/bodies", payload)
            if response is None:
                return
            if not stale:
                self._manifest_length[chat_id] = len(manifest)
            needed = (response.get("reconcile") or {}).get("needed_bodies") or []
            missing = [
                self._body(ref["host_logical_id"], by_id[ref["host_logical_id"]], item)
                for ref in needed
                if isinstance(ref, dict)
                and ref.get("host_logical_id") in by_id
                and ref.get("host_logical_id") != item.event_id
            ]
            if missing:
                await self._post(
                    "/v1/sync/bodies", {"host": HOST, "chat_id": chat_id, "bodies": missing}
                )

    @staticmethod
    def _body(event_id: str, body: dict[str, str], item: MemoryWrite) -> dict[str, Any]:
        return {
            "host_logical_id": event_id,
            "revision_hash": body["hash"],
            "content": body["text"],
            "metadata": {"world_id": item.world_id, "source": "saga-committed-facts"},
        }

    async def retrieve(
        self,
        *,
        world_id: str,
        query: str,
        actor_scope: str | None = None,
        limit: int = 8,
        branch_id: str = "main",
    ) -> list[MemoryHit]:
        if not query.strip():
            return []
        response = await self._post(
            "/v1/retrieve",
            {
                "host": HOST,
                "chat_id": f"{world_id}/{branch_id}",
                "query": query,
                "budget_tokens": max(1, limit) * _TOKENS_PER_HIT,
            },
        )
        if response is None:
            return []
        packet = response.get("packet") or {}
        text = packet.get("text") if isinstance(packet, dict) else None
        if not isinstance(text, str) or not text.strip():
            return []
        return [
            MemoryHit(
                text=text,
                metadata={
                    "trace_id": response.get("trace_id"),
                    "freshness": response.get("freshness"),
                    "excerpt_count": packet.get("excerpt_count"),
                },
            )
        ]
