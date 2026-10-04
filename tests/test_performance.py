import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from saga_trpg.adapters.narrator.fallback import FallbackNarrator
from saga_trpg.adapters.storage.sqlite import SQLiteWorldRepository
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.ports.narrator import NarrationRequest
from saga_trpg.presentation.models import DialogueMetadata, NarrativeSource, PerformanceEvent
from saga_trpg.presentation.segmentation import segment_narrative
from saga_trpg.presentation.service import present_narrative
from saga_trpg.runtime.rng import DeterministicDice

SOURCE = NarrativeSource(
    world_id="campaign", branch_id="main", game_event_id="turn-1", narration_id="reply-1"
)
ACTORS = frozenset({"player", "sera", "companion"})


def metadata(*spans):
    return json.dumps({"schema_version": 1, "spans": spans}, ensure_ascii=False)


def dialogue(start, end, actor_id=None, *, kind="actor"):
    return {"start": start, "end": end, "speaker": {"kind": kind, "actor_id": actor_id}}


class RecordingConsumer:
    def __init__(self):
        self.events = {}
        self.calls = 0

    async def consume(self, events):
        self.calls += 1
        for event in events:
            # Consumers upsert identities; retrying delivery must not replay an effect.
            self.events.setdefault(event.event_id, event)


def test_unannotated_prose_and_tags_are_lossless_unclassified_text():
    text = '  “Hello,” she said.\n[[SPEECH speaker="sera"]]unfinished 🐉\n'
    result = segment_narrative(text, source=SOURCE, actor_ids=ACTORS)
    assert result.metadata_status == "absent"
    assert len(result.events) == 1
    event = result.events[0]
    assert (event.kind, event.speaker.kind, event.text) == ("text", "unresolved", text)
    assert (event.start, event.end) == (0, len(text))


def test_structured_spans_preserve_unicode_gaps_and_canonical_speakers():
    text = "🐉 앞. 안녕!\n어서 와. 뒤."
    first, second = text.index("안녕"), text.index("어서")
    result = segment_narrative(
        text,
        source=SOURCE,
        actor_ids=ACTORS,
        annotations_json=metadata(
            dialogue(first, first + 3, "sera"), dialogue(second, second + 5, "player")
        ),
    )
    assert result.metadata_status == "accepted"
    assert "".join(event.text for event in result.events) == text
    assert [event.segment_index for event in result.events] == list(range(5))
    spoken = [event for event in result.events if event.kind == "dialogue"]
    assert [(event.text, event.speaker.actor_id) for event in spoken] == [
        ("안녕!", "sera"),
        ("어서 와.", "player"),
    ]


@pytest.mark.parametrize(
    "speaker,expected",
    [
        ({"kind": "actor", "actor_id": "player"}, ("actor", "player")),
        ({"kind": "actor", "actor_id": "companion"}, ("actor", "companion")),
        ({"kind": "actor", "actor_id": "sera"}, ("actor", "sera")),
        ({"kind": "actor", "actor_id": "Sera"}, ("unresolved", None)),
        ({"kind": "actor", "actor_id": "invented-uuid"}, ("unresolved", None)),
        ({"kind": "narrator", "actor_id": None}, ("narrator", None)),
        ({"kind": "unresolved", "actor_id": None}, ("unresolved", None)),
    ],
)
def test_speaker_resolution_is_exact_and_never_creates_entities(speaker, expected):
    result = segment_narrative(
        "hello",
        source=SOURCE,
        actor_ids=ACTORS,
        annotations_json=metadata({"start": 0, "end": 5, "speaker": speaker}),
    )
    assert result.metadata_status == "accepted"
    assert (result.events[0].speaker.kind, result.events[0].speaker.actor_id) == expected


@pytest.mark.parametrize(
    "annotations",
    [
        "{",
        "[]",
        "null",
        '{"schema_version":2,"spans":[]}',
        '{"schema_version":true,"spans":[]}',
        '{"schema_version":1.0,"spans":[]}',
        '{"schema_version":1,"spans":[],"world_state":{}}',
        metadata(dialogue(True, 3, "sera")),
        metadata(dialogue("0", 3, "sera")),
        metadata(dialogue(-1, 3, "sera")),
        metadata(dialogue(0, 0, "sera")),
        metadata(dialogue(0, 99, "sera")),
        metadata(dialogue(0, 3, "sera"), dialogue(2, 5, "player")),
        metadata(dialogue(3, 5, "sera"), dialogue(0, 2, "player")),
        metadata(dialogue(0, 3)),
        metadata(dialogue(0, 3, "sera", kind="narrator")),
        metadata(
            {"start": 0, "end": 3, "speaker": {"kind": "actor", "actor_id": "sera", "hp": 99}}
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_metadata_never_breaks_text_or_partially_applies(annotations):
    result = await present_narrative(
        "hello", source=SOURCE, actor_ids=ACTORS, annotations_json=annotations, enabled=True
    )
    assert result.text == "hello"
    assert result.metadata_status == "invalid"
    assert result.delivery_status == "disabled"
    assert len(result.events) == 1
    assert result.events[0].speaker.kind == "unresolved"


def test_empty_text_and_adjacent_spans():
    assert segment_narrative("", source=SOURCE, actor_ids=ACTORS).events == ()
    result = segment_narrative(
        "ab",
        source=SOURCE,
        actor_ids=ACTORS,
        annotations_json=metadata(dialogue(0, 1, "sera"), dialogue(1, 2, "player")),
    )
    assert [event.text for event in result.events] == ["a", "b"]


@pytest.mark.parametrize(
    "annotations",
    [
        " " * 65_536 + metadata(),
        metadata(*({"start": index, "end": index + 1} for index in range(1025))),
    ],
)
def test_oversized_metadata_falls_back_to_text(annotations):
    text = "a" * 1025
    result = segment_narrative(text, source=SOURCE, actor_ids=ACTORS, annotations_json=annotations)
    assert result.metadata_status == "invalid"
    assert len(result.events) == 1
    assert result.events[0].text == text


def test_versioned_serialization_and_id_scope():
    event = segment_narrative("hello", source=SOURCE, actor_ids=ACTORS).events[0]
    assert event.event_id == (
        "performance:2d4c15edcec1c64e4b19a9293e1e1935dbe53a60ee58403c8b6ff176cb4f0012"
    )
    assert PerformanceEvent.model_validate_json(event.model_dump_json()) == event
    assert segment_narrative("hello", source=SOURCE, actor_ids=ACTORS).events[0] == event
    ids = {event.event_id}
    for field in ("world_id", "branch_id", "game_event_id", "narration_id"):
        changed = SOURCE.model_copy(update={field: "other"})
        ids.add(segment_narrative("hello", source=changed, actor_ids=ACTORS).events[0].event_id)
    ids.add(segment_narrative("hello!", source=SOURCE, actor_ids=ACTORS).events[0].event_id)
    ids.add(
        segment_narrative(
            "hello",
            source=SOURCE,
            actor_ids=ACTORS,
            annotations_json=metadata(dialogue(0, 5, "sera")),
        )
        .events[0]
        .event_id
    )
    assert len(ids) == 7
    with pytest.raises(ValidationError, match="literal_error"):
        PerformanceEvent.model_validate({**event.model_dump(), "schema_version": 2})


def test_exported_schemas_match_models():
    root = Path(__file__).resolve().parents[1] / "schemas"
    for name, model in (
        ("performance-event", PerformanceEvent),
        ("dialogue-metadata", DialogueMetadata),
    ):
        saved = json.loads((root / f"{name}.schema.json").read_text())
        assert saved.pop("$schema") == "https://json-schema.org/draft/2020-12/schema"
        assert saved == model.model_json_schema()


@pytest.mark.asyncio
async def test_default_disabled_skips_segmentation_and_consumer(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled enrichment must not segment text")

    monkeypatch.setattr("saga_trpg.presentation.service.segment_narrative", forbidden)
    consumer = RecordingConsumer()
    result = await present_narrative(
        "unchanged", source=SOURCE, actor_ids=ACTORS, annotations_json="{", consumer=consumer
    )
    assert result.text == "unchanged"
    assert result.events == ()
    assert result.metadata_status == "disabled"
    assert result.delivery_status == "disabled"
    assert consumer.calls == 0


@pytest.mark.asyncio
async def test_repeated_projection_has_stable_ids_for_idempotent_consumption():
    consumer = RecordingConsumer()
    for _ in range(3):
        result = await present_narrative(
            "hello", source=SOURCE, actor_ids=ACTORS, enabled=True, consumer=consumer
        )
        assert result.delivery_status == "delivered"
    assert consumer.calls == 3
    assert len(consumer.events) == 1


@pytest.mark.asyncio
async def test_consumer_failure_is_fail_open_without_retry_or_private_logs(caplog):
    class FailedConsumer:
        calls = 0

        async def consume(self, events):
            self.calls += 1
            raise RuntimeError("private dialogue and provider credentials")

    consumer = FailedConsumer()
    result = await present_narrative(
        "private dialogue", source=SOURCE, actor_ids=ACTORS, enabled=True, consumer=consumer
    )
    assert result.text == "private dialogue"
    assert result.delivery_status == "failed"
    assert consumer.calls == 1
    assert "private dialogue" not in caplog.text
    assert "credentials" not in caplog.text


@pytest.mark.asyncio
async def test_consumer_timeout_and_cancellation():
    entered = asyncio.Event()
    stopped = asyncio.Event()

    class WaitingConsumer:
        async def consume(self, events):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    result = await present_narrative(
        "hello",
        source=SOURCE,
        actor_ids=ACTORS,
        enabled=True,
        consumer=WaitingConsumer(),
        timeout_seconds=0.01,
    )
    assert result.delivery_status == "timeout"
    assert result.text == "hello"
    assert stopped.is_set()
    entered.clear()
    task = asyncio.create_task(
        present_narrative(
            "hello",
            source=SOURCE,
            actor_ids=ACTORS,
            enabled=True,
            consumer=WaitingConsumer(),
            timeout_seconds=10,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_narrator_to_consumer_rerenders_preserve_sqlite_state_journal_and_dice(tmp_path):
    class CountingDice(DeterministicDice):
        calls = 0

        def roll(self, **kwargs):
            self.calls += 1
            return super().roll(**kwargs)

    dice = CountingDice("performance-test")
    repo = SQLiteWorldRepository(tmp_path / "worlds.sqlite3", dice=dice)
    repo.create_world(
        WorldState(
            world_id="campaign",
            actors={"player": ActorState(actor_id="player"), "sera": ActorState(actor_id="sera")},
        )
    )
    repo.dispatch(
        "campaign",
        GameEvent(
            event_id="turn-1",
            kind="attack",
            actor_id="player",
            payload={"target_id": "sera", "attack_bonus": 20, "target_ac": 10, "damage": "1d6"},
        ),
    )
    before, history, calls = repo.get_state("campaign"), repo.entries("campaign"), dice.calls
    assert calls > 0

    class MutationAttempt:
        async def consume(self, events):
            events[0].speaker.actor_id = "invented"

    for index in range(10):
        entry = repo.get_event("campaign", "turn-1")
        text = await FallbackNarrator().render(
            NarrationRequest(world_id="campaign", event=entry.committed.event, result=entry.result)
        )
        source = SOURCE.model_copy(update={"narration_id": f"reply-{index}"})
        actor_ids = frozenset(
            key for key, actor in entry.result.state.actors.items() if key == actor.actor_id
        )
        result = await present_narrative(
            text,
            source=source,
            actor_ids=actor_ids,
            enabled=index != 0,
            consumer=MutationAttempt(),
        )
        assert result.text == text
        assert result.delivery_status == ("disabled" if index == 0 else "failed")
        assert repo.get_state("campaign") == before
        assert repo.entries("campaign") == history
    restarted = SQLiteWorldRepository(repo.path, dice=dice)
    assert restarted.get_state("campaign") == restarted.replay("campaign") == before
    assert restarted.entries("campaign") == history
    assert dice.calls == calls
