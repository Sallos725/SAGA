import hashlib
import json

import httpx
import pytest

from saga_trpg.adapters.memory.nmos import NMOSAdapter, history_entry
from saga_trpg.ports.memory import MemoryWrite


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeNMOS:
    """Enough of the sidecar 0.3.0 surface (reconcile, bodies, retrieve, health) to test against."""

    def __init__(self):
        self.manifests = {}
        self.bodies = {}
        self.calls = []
        self.fail_with = None
        self.packet = "The innkeeper believes the player still owes 30 silver."

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.fail_with is not None:
            raise self.fail_with
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"ok": True, "version": "0.3.0"})
        body = json.loads(request.content or b"{}")
        if request.url.path == "/v1/sync/reconcile":
            return httpx.Response(200, json=self._reconcile(body))
        if request.url.path == "/v1/sync/bodies":
            stored = 0
            for item in body["bodies"]:
                key = (body["chat_id"], item["host_logical_id"], item["revision_hash"])
                if key not in self.bodies:
                    self.bodies[key] = item
                    stored += 1
            response = {"ok": True, "stored": stored, "rejected": []}
            if body.get("then_reconcile"):
                response["reconcile"] = self._reconcile(body["then_reconcile"])
            return httpx.Response(200, json=response)
        if request.url.path == "/v1/retrieve":
            assert body["budget_tokens"] > 0 and body["query"]
            return httpx.Response(
                200,
                json={
                    "trace_id": "trace-1",
                    "freshness": "fresh",
                    "packet": {"text": self.packet, "token_estimate": 12, "excerpt_count": 1},
                },
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    def _reconcile(self, body):
        self.manifests[body["chat_id"]] = body["messages"]
        needed = [
            {"host_logical_id": m["host_logical_id"], "revision_hash": m["revision_hash"]}
            for m in body["messages"]
            if (body["chat_id"], m["host_logical_id"], m["revision_hash"]) not in self.bodies
        ]
        return {
            "conversation_id": "conv-" + body["chat_id"],
            "status": "ok",
            "active_commit": "c1",
            "manifest_hash": sha(json.dumps(body["messages"])),
            "needed_bodies": needed,
            "changes_summary": {},
        }


def adapter_for(fake, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), base_url="http://nmos")
    return NMOSAdapter(base_url="http://nmos", client=client, **kwargs)


def write(event_id, text, history):
    return MemoryWrite(
        world_id="world-1",
        event_id=event_id,
        text=text,
        metadata={"branch_id": "main", "history": history},
    )


@pytest.mark.asyncio
async def test_remember_syncs_the_full_manifest_and_only_missing_bodies():
    fake = FakeNMOS()
    adapter = adapter_for(fake)
    first = history_entry("e1", "player: attack hit against opponent; damage 4; HP 26.")
    await adapter.remember(write("e1", first["text"], [first]))
    assert fake.manifests["world-1/main"] == [
        {
            "host_logical_id": "e1",
            "revision_hash": sha(first["text"]),
            "role": "char",
            "name": "SAGA",
            "swipe_count": 1,
            "special_comments": [],
        }
    ]
    assert list(fake.bodies) == [("world-1/main", "e1", sha(first["text"]))]
    second = history_entry("e2", "player: damage 5 to opponent; HP 26->21/30.")
    await adapter.remember(write("e2", second["text"], [first, second]))
    assert [m["host_logical_id"] for m in fake.manifests["world-1/main"]] == ["e1", "e2"]
    assert len(fake.bodies) == 2
    # Re-remembering is idempotent: same hashes, nothing stored twice.
    await adapter.remember(write("e2", second["text"], [first, second]))
    assert len(fake.bodies) == 2
    assert fake.calls.count(("POST", "/v1/sync/bodies")) == 3


@pytest.mark.asyncio
async def test_remember_serves_bodies_the_sidecar_asks_for_after_its_own_restart():
    fake = FakeNMOS()
    adapter = adapter_for(fake)
    history = [history_entry("e1", "first"), history_entry("e2", "second")]
    # The sidecar lost everything; it will ask for both bodies even though only e2 is new.
    await adapter.remember(write("e2", "second", history))
    assert {key[1] for key in fake.bodies} == {"e1", "e2"}


@pytest.mark.asyncio
async def test_retrieve_returns_the_packet_as_one_hit_and_nothing_when_empty():
    fake = FakeNMOS()
    adapter = adapter_for(fake)
    hits = await adapter.retrieve(world_id="world-1", query="who do I owe?", limit=4)
    assert [hit.text for hit in hits] == [fake.packet]
    assert hits[0].metadata["trace_id"] == "trace-1"
    fake.packet = ""
    assert await adapter.retrieve(world_id="world-1", query="anything") == []


@pytest.mark.asyncio
async def test_failures_are_soft_and_never_raise(caplog):
    fake = FakeNMOS()
    fake.fail_with = httpx.ConnectTimeout("nmos is away")
    adapter = adapter_for(fake)
    await adapter.remember(write("e1", "text", [history_entry("e1", "text")]))
    assert await adapter.retrieve(world_id="world-1", query="q") == []
    assert any("NMOS" in record.message for record in caplog.records)
    fake.fail_with = None
    fake.packet = "back"
    assert [h.text for h in await adapter.retrieve(world_id="world-1", query="q")] == ["back"]


@pytest.mark.asyncio
async def test_health_reports_capability_or_absence():
    fake = FakeNMOS()
    adapter = adapter_for(fake)
    assert await adapter.health() == {"ok": True, "version": "0.3.0"}
    fake.fail_with = httpx.ConnectError("down")
    assert await adapter.health() is None


@pytest.mark.asyncio
async def test_remember_without_history_sends_a_single_message_manifest():
    fake = FakeNMOS()
    adapter = adapter_for(fake)
    await adapter.remember(MemoryWrite(world_id="w", event_id="e9", text="lonely fact"))
    assert [m["host_logical_id"] for m in fake.manifests["w/main"]] == ["e9"]


@pytest.mark.asyncio
async def test_a_stale_shorter_manifest_never_replaces_a_longer_one():
    fake = FakeNMOS()
    adapter = adapter_for(fake)
    e1, e2 = history_entry("e1", "first"), history_entry("e2", "second")
    await adapter.remember(write("e2", "second", [e1, e2]))
    assert [m["host_logical_id"] for m in fake.manifests["world-1/main"]] == ["e1", "e2"]
    # A delayed request for e1 arrives with its older, shorter manifest.
    await adapter.remember(write("e1", "first", [e1]))
    assert [m["host_logical_id"] for m in fake.manifests["world-1/main"]] == ["e1", "e2"]
    assert ("world-1/main", "e1", sha("first")) in fake.bodies
