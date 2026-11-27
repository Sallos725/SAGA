"""Versioned, no-memory PocketRisu bridge; all effects go through the repository."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from saga_trpg.adapters.intent.openai_compat import (
    INTENT_INSTRUCTION,
    OpenAICompatIntentTool,
)
from saga_trpg.adapters.intent.rulebook_grounding import (
    allowed_v4_models,
    ground_proposal,
    ground_proposal_or_narrative,
    intent_context_for,
)
from saga_trpg.adapters.intent.vertex import (
    PENDING,
    TARGET_ALIASES,
    VertexIntentTool,
    event_from_result,
)
from saga_trpg.adapters.memory.nmos import history_entry
from saga_trpg.adapters.memory.null import NullMemory
from saga_trpg.adapters.narrator.fallback import FallbackNarrator
from saga_trpg.adapters.narrator.openai_compat import OpenAICompatConfig, OpenAICompatNarrator
from saga_trpg.adapters.narrator.vertex import VertexNarrator
from saga_trpg.adapters.storage.sqlite import (
    EventLocation,
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
from saga_trpg.domain.events import NARRATIVE_TURN_KIND, GameEvent
from saga_trpg.domain.maps import known_map, scene_text
from saga_trpg.domain.models import WorldState
from saga_trpg.domain.tools import ToolCall
from saga_trpg.modules.srd_5_2_1.rules import ROLL_ACTIONS
from saga_trpg.ports.intent import IntentPort
from saga_trpg.ports.memory import MemoryHit, MemoryPort, MemoryWrite
from saga_trpg.ports.narrator import NarrationRequest, NarratorPort
from saga_trpg.ports.tools import ToolExecutionPending
from saga_trpg.rulebooks.commands import (
    CommandError,
    command_help,
    command_verbs,
    parse_command,
    rules_for,
)
from saga_trpg.rulebooks.display import render_display
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import RulebookSpec, load_rulebook, parse_rulebook, rulebook_path
from saga_trpg.rulebooks.scenarios import build_scenario_world, parse_scenario
from saga_trpg.runtime.hashing import stable_model_hash, stable_value_hash
from saga_trpg.runtime.journal import JournalEntry

logger = logging.getLogger(__name__)
Identity = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HostAction(ClosedModel):
    message_id: Identity
    text: str = Field(min_length=1, max_length=2048)
    context_hidden: bool = False


class RulebookAttachment(ClosedModel):
    """A rulebook carried by an enabled RisuAI module; pins a world when it is created."""

    content: str = Field(min_length=2, max_length=2_000_000)


class ScenarioAttachment(ClosedModel):
    """A scenario carried by a RisuAI scenario card/module; seeds a world on creation (M2)."""

    content: str = Field(min_length=2, max_length=2_000_000)


class HostTurn(ClosedModel):
    schema_version: Literal[1, 2, 3, 4]
    host_instance_id: Identity
    chat_id: Identity
    actor_id: Literal["player"]
    purpose: Literal["action"]
    actions: list[HostAction] = Field(min_length=1, max_length=256)
    rulebook: RulebookAttachment | None = None
    scenario: ScenarioAttachment | None = None

    @model_validator(mode="after")
    def distinct_messages(self) -> HostTurn:
        if self.scenario is not None and self.schema_version < 4:
            raise ValueError("scenario attachments require protocol v4")
        ids = [action.message_id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate host user-message identity")
        if self.actions[-1].context_hidden:
            raise ValueError("the latest user action is hidden; restore its visibility first")
        return self


class ClientProposal(ClosedModel):
    """Untrusted event proposal (e.g. from RisuAI aux model); always re-validated by SAGA."""

    kind: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_BLOCKED_LLM_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal", "metadata.internal"})


def _validate_intent_llm_url(url: str) -> None:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not host or host in _BLOCKED_LLM_HOSTS:
        raise ValueError("disallowed intent LLM endpoint host")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in _LOOPBACK_HOSTS:
        return
    raise ValueError("client-provided intent_llm base_url must use https:// or localhost http://")


class IntentLlmOverride(ClosedModel):
    """Per-request OpenAI-compatible intent LLM settings configured in the plugin UI.

    API keys travel in the `x-saga-llm-api-key` header (never stored in world state,
    journals, snapshots, or tool tapes)."""

    base_url: str = Field(min_length=8, max_length=512)
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: str | None = Field(default=None, max_length=20)
    custom_headers: dict[str, str] | None = None
    custom_body: dict[str, Any] | None = None
    timeout_seconds: float = Field(default=25.0, gt=0, le=60)


class TurnRequest(ClosedModel):
    """Hook-mode adjudication request: commits authoritative state without SAGA narration."""

    saga: HostTurn
    client_proposal: ClientProposal | None = None
    intent_llm: IntentLlmOverride | None = None


class IntentContextRequest(ClosedModel):
    """Read-only lookup for grounded natural-language intent classification (e.g. RisuAI aux)."""

    saga: HostTurn


class ChatMessage(ClosedModel):
    role: Literal["system", "user", "assistant", "developer"]
    content: str = Field(max_length=32000)


class VertexNarrationOptions(ClosedModel):
    schema_version: Literal[1]
    provider: Literal["vertex"]
    context: list[Annotated[str, Field(max_length=4000)]] = Field(
        default_factory=list, max_length=32
    )


class CompletionRequest(ClosedModel):
    model: Literal["saga-srd-demo"]
    messages: list[ChatMessage] = Field(min_length=1, max_length=512)
    stream: bool = False
    saga: HostTurn
    narration: VertexNarrationOptions | None = None
    risu_prompt: list[ChatMessage] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def matching_action(self) -> CompletionRequest:
        if self.stream and self.saga.schema_version < 4:
            raise ValueError("streaming requires protocol v4")
        if self.risu_prompt and self.saga.schema_version < 4:
            raise ValueError("risu_prompt requires protocol v4")
        latest = self.messages[-1]
        if latest.role != "user" or latest.content != self.saga.actions[-1].text:
            raise ValueError("last prompt message must match the identified user action exactly")
        return self


# The latest bundled SRD rulebook is the default for new worlds without an attached rulebook.
DEFAULT_SPEC = load_rulebook(rulebook_path("srd-5.2.1"))
# Worlds pinned to the hand-written SRD versions (0.1.0–0.3.0) derive command identities with
# this frozen grammar. Using the latest SRD would let a new verb (`move` in 0.6.0) turn an
# already committed natural-language action into a command and change its identity.
LEGACY_SRD_SPEC = load_rulebook(rulebook_path("srd-5.2.1", "0.5.0"))


def supported_commands(spec: RulebookSpec) -> str:
    return (
        f"Supported commands for {spec.id}@{spec.version}:\n{command_help(spec)}\n"
        "Values fill the inputs in order; name=value sets one by name; quote text with spaces. "
    )


def canonical_command(*, actor_id: str, text: str, spec: RulebookSpec = DEFAULT_SPEC) -> GameEvent:
    """Pure v2 identity contract; never call an injected/remote interpreter to derive an id."""
    try:
        return parse_command(spec, actor_id=actor_id, text=text)
    except CommandError as exc:
        raise ValueError(
            f"{exc}. {supported_commands(spec)}"
            "Natural language requires protocol 3 and Vertex JSON."
        ) from None


def command_for(
    action: HostAction, actor_id: str, *, natural: bool, spec: RulebookSpec = DEFAULT_SPEC
) -> GameEvent | None:
    """Preserve v2 command identities; raw text is a separate explicit v3 contract."""
    try:
        return canonical_command(actor_id=actor_id, text=action.text, spec=spec)
    except ValueError as exc:
        words = action.text.strip().lower().split()
        if natural and words and words[0] not in command_verbs(spec):
            if action.text.strip().lower() != "*says nothing*":
                return None
        if action.context_hidden:
            raise RepositoryConflict(
                "Hidden user history is not a committed supported action. "
                "Restore it for review; hidden drafts are never executed automatically."
            ) from None
        raise ValueError(
            f"{exc} Empty actions and malformed commands are not interpreted automatically."
        ) from None


def memory_projection(entry: JournalEntry) -> str:
    """What semantic memory may hear about a committed event: no numbers, no exact state.

    Actors, action kind, text-valued inputs (targets, conditions, items, names) and the
    recorded outcome are narrative; totals, damage, HP, counts and durations are state and
    stay in the journal ([ADR 0001](../../../docs/adr/0001-state-vs-memory.md))."""
    event = entry.committed.event
    details = [
        f"{key} {value}"
        for key, value in event.payload.items()
        if isinstance(value, str) and value and not value.isdigit()
    ]
    text = f"{event.actor_id}: {event.kind}"
    if details:
        text += " (" + ", ".join(details) + ")"
    resolution = entry.result.resolution
    if resolution is not None:
        text += f" -> {resolution.outcome}"
        if resolution.pending_adjudication:
            text += ", cost pending"
    return text + "."


class CommandInterpreter:
    """Closed rulebook commands, deliberately independent of generated prose."""

    async def interpret(
        self, *, world_id: str, actor_id: str, text: str, spec: RulebookSpec = DEFAULT_SPEC
    ) -> GameEvent:
        return canonical_command(actor_id=actor_id, text=text, spec=spec)


@dataclass
class CommittedTurn:
    schema_version: int
    identity_version: int
    legacy_read_only: bool
    world_id: str
    branch_id: str
    event_id: str
    replayed: bool
    entry: JournalEntry
    spec: RulebookSpec
    display: dict[str, Any]
    scene: str | None
    memories: list[MemoryHit]

    def prompt_block(self) -> str:
        """Read-only authoritative context for Hook mode (ADR 0009/0010).

        Never includes full map data or raw actor locations/visited_rooms; exposes only the
        player's known-map projection (`scene`), committed turn facts/rolls, and display lines."""
        event = self.entry.committed.event
        result = self.entry.result
        lines = [
            "[SAGA Authoritative State — Read-Only]",
            "Treat the committed facts, rolls, status, and known map below as ground truth.",
            "Never invent dice rolls, damage, item changes, or state mutations.",
            "Never narrate arriving in another room unless a move event was committed this turn.",
            "Do NOT print a status footer, dice/HP summary block, event ID, or HTML stat bar; "
            "SAGA appends the authoritative status footer automatically after your prose.",
            f"Turn event: {event.kind} (actor={event.actor_id}, seq={self.entry.committed.seq})",
        ]
        if result.facts:
            lines.append("Committed facts: " + "; ".join(result.facts))
        if result.rolls:
            rolls = ", ".join(
                f"{roll.roll_key}={roll.total} ({roll.expression}: {roll.raw})"
                for roll in result.rolls
            )
            lines.append("Rolls: " + rolls)
        if result.resolution is not None:
            res = result.resolution
            summary = f"{res.outcome} (total {res.total} vs target {res.target})"
            if res.damage is not None:
                summary += f", damage {res.damage}"
            if res.pending_adjudication:
                summary += ", cost pending adjudication"
            lines.append("Resolution: " + summary)
        status_lines: list[str] = []
        for actor in self.display.get("actors", ()):
            items = actor.get("items") or []
            if items:
                status_lines.append(f"{actor['id']}: {' · '.join(items)}")
        for record in self.display.get("records", ()):
            items = record.get("items") or []
            if items:
                status_lines.append(f"{record['label']}: {', '.join(items)}")
        for item in self.display.get("world", ()):
            status_lines.append(str(item))
        if status_lines:
            lines.append("Current status:")
            lines.extend(f"- {item}" for item in status_lines)
        if self.scene:
            lines.append(
                "Known map (describe nothing beyond visited rooms or behind ??? exits):\n"
                + self.scene
            )
        if self.memories:
            lines.append(
                "Recalled memory (non-authoritative story context; never override state):\n"
                + "\n".join(hit.text for hit in self.memories)
            )
        return "\n".join(lines)

    def turn_card(self) -> dict[str, Any]:
        """B1: immutable roll/result/change card derived strictly from this committed entry."""
        event = self.entry.committed.event
        result = self.entry.result
        changes = [f"{patch.path}: {patch.before} → {patch.after}" for patch in result.patches]
        return {
            "event_id": self.event_id,
            "seq": self.entry.committed.seq,
            "kind": event.kind,
            "actor_id": event.actor_id,
            "rolls": [roll.model_dump(mode="json") for roll in result.rolls],
            "resolution": (
                result.resolution.model_dump(mode="json") if result.resolution else None
            ),
            "changes": changes,
            "facts": list(result.facts),
        }

    def scene_board(self, viewer: str = "player") -> dict[str, Any]:
        """B2: active scene participant board; hides actors in unseen rooms (ADR 0009)."""
        state = self.entry.result.state
        view = known_map(state, viewer)
        if view is not None:
            visible_ids = [viewer, *view.occupants]
        else:
            visible_ids = sorted(state.actors)
        participants = []
        for aid in visible_ids:
            actor = state.actors.get(aid)
            if actor is None:
                continue
            participants.append(
                {
                    "actor_id": aid,
                    "display_name": actor.display_name,
                    "aliases": list(actor.aliases),
                    "is_player": aid == viewer,
                    "hp": actor.hp,
                    "max_hp": actor.max_hp,
                    "conditions": sorted(actor.conditions),
                    "status": "incapacitated" if actor.hp <= 0 else "active",
                }
            )
        quests = []
        for qid, qval in sorted(state.quest_state.items()):
            if isinstance(qval, dict):
                quests.append(
                    {
                        "quest_id": qid,
                        "stage": qval.get("stage"),
                        "status": qval.get("status", "active"),
                        "objective": qval.get("objective"),
                    }
                )
            else:
                quests.append({"quest_id": qid, "status": str(qval)})
        return {
            "current_room": view.room_name if view is not None else None,
            "exits": (
                [
                    {
                        "key": e.key,
                        "label": e.label,
                        "locked": e.locked,
                        "destination": e.destination,
                    }
                    for e in view.exits
                ]
                if view is not None
                else []
            ),
            "room_loot": view.loot if view is not None else {},
            "discovered_clues": view.discovered_clues if view is not None else {},
            "participants": participants,
            "quests": quests,
        }

    def saga_payload(self, *, include_prompt_block: bool = False) -> dict:
        data = {
            "schema_version": self.schema_version,
            "identity_version": self.identity_version,
            "legacy_read_only": self.legacy_read_only,
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "event_id": self.event_id,
            "seq": self.entry.committed.seq,
            "replayed": self.replayed,
            # Footer lines rendered from the rulebook's display declaration; the host
            # prints them without knowing what a stat or a record means.
            "display": self.display,
            **self.entry.result.model_dump(mode="json"),
        }
        if include_prompt_block:
            data["scene"] = self.scene
            data["prompt_block"] = self.prompt_block()
            data["turn_card"] = self.turn_card()
            data["scene_board"] = self.scene_board(viewer=self.entry.committed.event.actor_id)
        elif self.schema_version >= 4:
            data["turn_card"] = self.turn_card()
            data["scene_board"] = self.scene_board(viewer=self.entry.committed.event.actor_id)
        return data


class RisuBridge:
    def __init__(
        self,
        repository: SQLiteWorldRepository,
        *,
        interpreter: IntentPort | None = None,
        narrator: NarratorPort | None = None,
        memory: MemoryPort | None = None,
        openai_intent: OpenAICompatIntentTool | None = None,
    ) -> None:
        self.repository = repository
        self.interpreter = interpreter or CommandInterpreter()
        self.narrator = narrator or FallbackNarrator()
        # Semantic memory is optional and fail-soft: it never gates a commit or a response.
        self.memory = memory or NullMemory()
        self.openai_intent = openai_intent

    @staticmethod
    def world_id_for(turn: HostTurn, *, version: Literal[1, 2, 3, 4] | None = None) -> str:
        version = version or (turn.schema_version if turn.schema_version in (3, 4) else 2)
        world_version = 1 if version == 1 else 2
        return "risu-" + stable_value_hash(
            [f"saga-risu-v{world_version}", turn.host_instance_id, turn.chat_id]
        )

    @staticmethod
    def identities(
        turn: HostTurn,
        *,
        version: Literal[1, 2, 3, 4] | None = None,
        spec: RulebookSpec = DEFAULT_SPEC,
    ) -> tuple[str, list[str]]:
        version = version or (turn.schema_version if turn.schema_version in (3, 4) else 2)
        world_id = RisuBridge.world_id_for(turn, version=version)
        identities = []
        parent = None
        for action in turn.actions:
            content: str | dict = action.text
            action_version = version
            if version != 1:
                event = command_for(action, turn.actor_id, natural=version in (3, 4), spec=spec)
                if event is None:
                    content = {
                        "kind": "natural_language",
                        "schema_version": 1,
                        "actor_id": turn.actor_id,
                        "text": action.text,
                    }
                    action_version = 3
                else:
                    content = {
                        "kind": event.kind,
                        "actor_id": event.actor_id,
                        "payload": event.payload,
                    }
                    action_version = 2
            parent = "risu-" + stable_value_hash(
                [
                    f"saga-risu-action-v{action_version}",
                    world_id,
                    parent,
                    action.message_id,
                    content,
                ]
            )
            identities.append(parent)
        return world_id, identities

    def _spec_for(self, world_id: str, turn: HostTurn) -> RulebookSpec:
        """The rulebook that governs this world's commands.

        An existing world keeps the rulebook it was created with. A new world takes the
        attached rulebook (validated and registered first) or the bundled SRD. Worlds pinned
        to a hand-written SRD version parse commands with the frozen `LEGACY_SRD_SPEC`
        grammar, whose payload shapes are identical for the actions those versions have and
        whose verbs never change, so their action identities stay stable."""
        try:
            selection = self.repository.ruleset_for_world(world_id)
        except RepositoryNotFound:
            if turn.rulebook is None:
                return DEFAULT_SPEC
            manifest, _ = self.repository.register_rulebook(turn.rulebook.content)
            selection = manifest
        rules = self.repository.registry.get(selection.ruleset_id, selection.ruleset_version).rules
        return rules.spec if isinstance(rules, DataRules) else LEGACY_SRD_SPEC

    def _initial_world_preview(
        self, world_id: str, spec: RulebookSpec, scenario: ScenarioAttachment | None = None
    ) -> WorldState:
        """Pure in-memory initial state for a world (used for validation or creation)."""
        if scenario is not None:
            return build_scenario_world(parse_scenario(scenario.content), world_id, rulebook=spec)
        definition = self.repository.registry.get(spec.id, spec.version)
        return definition.example_world(world_id)

    def _ensure_world(
        self, world_id: str, spec: RulebookSpec, scenario: ScenarioAttachment | None = None
    ) -> WorldState:
        # New worlds pin the given rulebook (and optional scenario); existing worlds keep the
        # version and initial world they were created with, so semantics never shift under them.
        try:
            self.repository.ruleset_for_world(world_id)
            return self.repository.get_initial_state(world_id)
        except RepositoryNotFound:
            pass
        initial = self._initial_world_preview(world_id, spec, scenario)
        self.repository.create_world(
            initial,
            if_missing=True,
            ruleset_id=spec.id,
            ruleset_version=spec.version,
        )
        # A racing first request may have pinned this chat to another rulebook; identities
        # and events were derived with ours, so refuse rather than continue under theirs.
        selection = self.repository.ruleset_for_world(world_id)
        if (selection.ruleset_id, selection.ruleset_version) != (spec.id, spec.version):
            raise RepositoryConflict(
                f"this chat was just pinned to {selection.ruleset_id}@{selection.ruleset_version}"
                "; regenerate to continue under that rulebook"
            )
        return self.repository.get_initial_state(world_id)

    async def _natural_intent(
        self,
        world_id: str,
        identities: list[str],
        turn: HostTurn,
        parent: JournalEntry | None,
        vertex: VertexNarrator,
        *,
        allow_narrative_fallback: bool = False,
    ) -> GameEvent:
        initial = self._ensure_world(world_id, DEFAULT_SPEC)
        # A branch head can move while inference runs. Only the verified committed prefix
        # (or immutable ruleset genesis) supplies the snapshot bound to this interpretation.
        state = parent.result.state if parent else initial
        call = ToolCall(
            world_id=world_id,
            branch_id="main",
            event_id=identities[-1],
            tool_call_key="risu-natural-intent-v1",
            tool_name="vertex-intent",
            tool_version="1",
            protocol_version="saga-risu-intent-v1",
            arguments={
                "text": turn.actions[-1].text,
                "actor_id": turn.actor_id,
                "targets": {
                    key: aliases for key, aliases in TARGET_ALIASES.items() if key in state.actors
                },
            },
            context_hash=stable_model_hash(state),
        )
        try:
            execution = await asyncio.wait_for(
                asyncio.to_thread(
                    self.repository.tool_tape.execute, call, VertexIntentTool(vertex)
                ),
                timeout=27,
            )
        except (TimeoutError, ToolExecutionPending):
            # Cancellation does not revoke a durable claim or allow another remote attempt.
            raise ToolExecutionPending(PENDING) from None
        try:
            return event_from_result(execution.result, call.arguments)
        except ValueError:
            if allow_narrative_fallback:
                return GameEvent(kind=NARRATIVE_TURN_KIND, actor_id=turn.actor_id, payload={})
            raise

    async def _openai_natural_intent(
        self,
        world_id: str,
        identities: list[str],
        turn: HostTurn,
        parent: JournalEntry | None,
        spec: RulebookSpec,
        *,
        intent_tool: OpenAICompatIntentTool | None = None,
    ) -> GameEvent:
        tool = intent_tool or self.openai_intent
        assert tool is not None
        initial = self._ensure_world(world_id, spec, turn.scenario)
        state = parent.result.state if parent else initial
        call = ToolCall(
            world_id=world_id,
            branch_id="main",
            event_id=identities[-1],
            tool_call_key="risu-openai-intent-v2",
            tool_name="openai-compat-intent",
            tool_version="2",
            protocol_version="saga-risu-openai-intent-v2",
            arguments=intent_context_for(
                text=turn.actions[-1].text,
                actor_id=turn.actor_id,
                spec=spec,
                state=state,
                allow_adjudicate=True,
            ),
            context_hash=stable_model_hash(state),
        )
        try:
            execution = await asyncio.wait_for(
                asyncio.to_thread(self.repository.tool_tape.execute, call, tool),
                timeout=27,
            )
        except (TimeoutError, ToolExecutionPending):
            raise ToolExecutionPending(PENDING) from None
        return ground_proposal_or_narrative(
            execution.result,
            spec=spec,
            state=state,
            actor_id=turn.actor_id,
            allow_adjudicate=True,
        )

    def _preview_spec_for(self, world_id: str, turn: HostTurn) -> RulebookSpec:
        """Pure read-only rulebook lookup (never mutates storage or registers rulebooks)."""
        try:
            selection = self.repository.ruleset_for_world(world_id)
        except RepositoryNotFound:
            if turn.rulebook is None:
                return DEFAULT_SPEC
            return parse_rulebook(json.loads(turn.rulebook.content))
        rules = self.repository.registry.get(selection.ruleset_id, selection.ruleset_version).rules
        return rules.spec if isinstance(rules, DataRules) else LEGACY_SRD_SPEC

    def prepare_intent_context(self, turn: HostTurn) -> dict[str, Any]:
        """Read-only intent context for client-side classifiers (e.g. RisuAI submodel).

        Checks the committed journal first so regenerations make zero auxiliary LLM calls,
        and skips explicit deterministic commands."""
        if turn.schema_version != 4:
            return {
                "already_committed": False,
                "is_command": True,
                "system_instruction": None,
                "context": None,
            }
        legacy_world, legacy_ids = self.identities(turn, version=1)
        try:
            self.repository.ruleset_for_world(legacy_world)
            if self._read_prefix(legacy_world, legacy_ids) is not None:
                return {
                    "already_committed": True,
                    "is_command": False,
                    "system_instruction": None,
                    "context": None,
                }
        except RepositoryNotFound:
            pass
        world_id = self.world_id_for(turn)
        spec = self._preview_spec_for(world_id, turn)
        world_id, identities = self.identities(turn, spec=spec)
        if self._read_prefix(world_id, identities) is not None:
            return {
                "already_committed": True,
                "is_command": False,
                "system_instruction": None,
                "context": None,
            }
        expected = command_for(turn.actions[-1], turn.actor_id, natural=True, spec=spec)
        if expected is not None:
            return {
                "already_committed": False,
                "is_command": True,
                "system_instruction": None,
                "context": None,
            }
        parent = self._read_prefix(world_id, identities[:-1]) if len(identities) > 1 else None
        if parent is not None:
            preview_state = parent[1].result.state
        else:
            try:
                preview_state = self.repository.get_initial_state(world_id)
            except RepositoryNotFound:
                preview_state = self._initial_world_preview(world_id, spec, turn.scenario)
        return {
            "already_committed": False,
            "is_command": False,
            "system_instruction": INTENT_INSTRUCTION,
            "context": intent_context_for(
                text=turn.actions[-1].text,
                actor_id=turn.actor_id,
                spec=spec,
                state=preview_state,
                allow_adjudicate=True,
            ),
        }

    def _read_prefix(self, world_id: str, identities: list[str]) -> tuple[str, JournalEntry] | None:
        """The committed latest action and its branch, verified against the whole prefix."""
        if not identities:
            return None
        try:
            found = self.repository.locate_event(world_id, identities[-1])
        except RepositoryNotFound:
            return None
        if not found:
            return None
        # Report the branch that committed the action rather than a fork that inherited it.
        for item in sorted(found, key=lambda item: (item.seq <= item.fork_seq, item.branch_id)):
            entries = self._matching(world_id, item, identities)
            if entries is not None:
                return item.branch_id, entries[-1]
        raise RepositoryConflict("SAGA branch history conflicts with host action identities")

    def _matching(
        self, world_id: str, item: EventLocation, identities: list[str]
    ) -> list[JournalEntry] | None:
        """The branch prefix through a located event, only if it is exactly these actions."""
        if item.seq != len(identities):
            return None
        entries = self.repository.entries(world_id, branch_id=item.branch_id, through_seq=item.seq)
        if [entry.committed.event.event_id for entry in entries] != identities:
            return None
        return entries

    def _branch_for(self, world_id: str, identities: list[str]) -> tuple[str, bool]:
        """Continue the branch whose head is the parent; fork only when history diverges.

        Returns the branch and whether this call created it.

        Explicit event ids are branch scoped, so a branch merely containing the parent id is
        not trusted: the branch written to, or forked from, must hold the exact parent prefix.
        """
        event_id, prefix = identities[-1], identities[:-1]
        found = self.repository.locate_event(world_id, prefix[-1] if prefix else None)
        heads = [
            item
            for item in found
            if item.seq == item.head_seq
            and item.pending_event_id in (None, event_id)
            # An empty fork is reserved for the action it is named after.
            and (
                item.branch_id in ("main", event_id)
                or item.head_seq > item.fork_seq
                or item.pending_event_id == event_id
            )
        ]
        heads.sort(key=lambda item: (item.pending_event_id != event_id, item.branch_id))
        for item in heads:
            if self._matching(world_id, item, prefix) is not None:
                return item.branch_id, False
        for item in found:
            if self._matching(world_id, item, prefix) is not None:
                self.repository.fork(
                    world_id,
                    branch_id=event_id,
                    source_branch_id=item.branch_id,
                    at_event_id=prefix[-1] if prefix else None,
                    # A racing identical request may have committed since this request read.
                    unless_committed=event_id,
                )
                return event_id, True
        raise RepositoryConflict("SAGA branch history conflicts with host action identities")

    async def _remember(self, world_id: str, branch_id: str, saved: JournalEntry) -> None:
        """Write a semantic projection of the committed event with the branch's history.

        The history lets a stateless adapter rebuild its manifest and serve any body a memory
        service asks for. Exact numbers never leave the journal: `memory_projection` keeps
        who did what to whom and how it turned out, not totals, HP or counts."""
        if isinstance(self.memory, NullMemory):
            return
        try:
            entries = self.repository.entries(world_id, branch_id=branch_id)
            history = [
                history_entry(entry.committed.event.event_id, memory_projection(entry))
                for entry in entries
            ]
            text = memory_projection(saved)
            await self.memory.remember(
                MemoryWrite(
                    world_id=world_id,
                    event_id=saved.committed.event.event_id,
                    text=text,
                    metadata={"branch_id": branch_id, "history": history},
                )
            )
        except Exception:
            logger.warning("memory write failed after commit; continuing without it")

    def _is_head(self, world_id: str, branch_id: str, saved: JournalEntry) -> bool:
        """Only the branch head may recall: a historical regeneration must not see later facts."""
        for item in self.repository.locate_event(world_id, saved.committed.event.event_id):
            if item.branch_id == branch_id:
                return item.seq == item.head_seq
        return False

    async def _recall(self, world_id: str, branch_id: str, query: str) -> list[MemoryHit]:
        if isinstance(self.memory, NullMemory):
            return []
        try:
            hits = await self.memory.retrieve(
                world_id=world_id, query=query, branch_id=branch_id, limit=4
            )
            return [MemoryHit.model_validate(hit.model_dump()) for hit in hits]
        except Exception:
            logger.warning("memory recall failed; narrating without memories")
            return []

    async def commit_turn(
        self,
        turn: HostTurn,
        *,
        vertex_narrator: VertexNarrator | None = None,
        client_proposal: ClientProposal | None = None,
        intent_tool: OpenAICompatIntentTool | None = None,
    ) -> CommittedTurn:
        replayed = True
        spec = DEFAULT_SPEC
        legacy_world, legacy_ids = self.identities(turn, version=1)
        try:
            self.repository.ruleset_for_world(legacy_world)
            legacy_exists = True
        except RepositoryNotFound:
            legacy_exists = False
        identity_version = 1 if legacy_exists else 2
        if legacy_exists:
            world_id, identities = legacy_world, legacy_ids
            saved = self._read_prefix(world_id, identities)
            if saved is None:
                raise RepositoryConflict(
                    "This chat has read-only v1 history. Only its exact original text and "
                    "message-id prefix can replay. Restore that history, or start a new host "
                    "chat for v2 commands. Legacy cosmetic edits cannot be reconstructed safely."
                )
        else:
            if turn.schema_version == 1:
                raise RepositoryConflict(
                    "Bridge v1 is read-only. Update the plugin to protocol v2 for new chats."
                )
            spec = self._spec_for(self.world_id_for(turn), turn)
            world_id, identities = self.identities(turn, spec=spec)
            if turn.schema_version in (3, 4) and any(
                command_for(action, turn.actor_id, natural=True, spec=spec) is None
                for action in turn.actions
            ):
                identity_version = 3
            for index, action in enumerate(turn.actions):
                if (
                    action.context_hidden
                    and self._read_prefix(world_id, identities[: index + 1]) is None
                ):
                    raise RepositoryConflict(
                        "Hidden history has no matching committed prefix. Restore the first "
                        "hidden/edited turn for review, or remove a never-executed draft. "
                        "No hidden action was executed and no campaign was reset."
                    )
            saved = self._read_prefix(world_id, identities)
        event_id = identities[-1]
        if saved is None:
            parent = self._read_prefix(world_id, identities[:-1]) if len(identities) > 1 else None
            if len(identities) > 1 and parent is None:
                raise RepositoryConflict(
                    "Uncommitted or edited earlier history. Rewind the host chat to the first "
                    "changed user turn and generate there before adding later actions."
                )
            expected = command_for(
                turn.actions[-1],
                turn.actor_id,
                natural=turn.schema_version in (3, 4),
                spec=spec,
            )
            if expected is None:
                if turn.schema_version == 4:
                    if client_proposal is not None:
                        if parent is not None:
                            preview_state = parent[1].result.state
                        else:
                            try:
                                preview_state = self.repository.get_initial_state(world_id)
                            except RepositoryNotFound:
                                preview_state = self._initial_world_preview(
                                    world_id, spec, turn.scenario
                                )
                        proposed = ground_proposal(
                            client_proposal.model_dump(),
                            spec=spec,
                            state=preview_state,
                            actor_id=turn.actor_id,
                            allow_adjudicate=True,
                        )
                    elif intent_tool is not None or self.openai_intent is not None:
                        proposed = await self._openai_natural_intent(
                            world_id,
                            identities,
                            turn,
                            parent[1] if parent else None,
                            spec,
                            intent_tool=intent_tool,
                        )
                    elif vertex_narrator is not None and spec.id == DEFAULT_SPEC.id:
                        proposed = await self._natural_intent(
                            world_id,
                            identities,
                            turn,
                            parent[1] if parent else None,
                            vertex_narrator,
                            allow_narrative_fallback=True,
                        )
                    else:
                        proposed = GameEvent(
                            kind=NARRATIVE_TURN_KIND, actor_id=turn.actor_id, payload={}
                        )
                else:
                    if spec.id != DEFAULT_SPEC.id:
                        raise ValueError(
                            "Natural-language actions are only interpreted for the SRD demo "
                            f"rulebook; use this rulebook's commands. {supported_commands(spec)}"
                        )
                    if vertex_narrator is None:
                        raise ValueError(
                            "자연어 행동에는 Vertex 서비스 계정 JSON이 필요합니다. "
                            "명령은 키 없이 사용할 수 있습니다."
                        )
                    proposed = await self._natural_intent(
                        world_id,
                        identities,
                        turn,
                        parent[1] if parent else None,
                        vertex_narrator,
                    )
            else:
                arguments = {
                    "world_id": world_id,
                    "actor_id": turn.actor_id,
                    "text": turn.actions[-1].text,
                }
                if isinstance(self.interpreter, CommandInterpreter):
                    arguments["spec"] = spec
                proposed = await self.interpreter.interpret(**arguments)
                proposed = GameEvent.model_validate(proposed.model_dump())
            # Natural-language proposals stay limited to SRD roll actions under v3; v4 allows
            # validated rulebook actions, adjudicate_action (A1), or a state-neutral
            # narrative_turn; commands cover the whole rulebook.
            if expected is not None:
                allowed = rules_for(spec).payload_models
            elif turn.schema_version == 4:
                allowed = allowed_v4_models(spec, allow_adjudicate=True)
            else:
                allowed = ROLL_ACTIONS
            payload_model = allowed.get(proposed.kind)
            if payload_model is None or proposed.actor_id != turn.actor_id:
                raise ValueError("intent must select a supported action for the identified actor")
            payload = payload_model.model_validate(proposed.payload)
            if expected is not None and (
                proposed.kind != expected.kind or payload.model_dump() != expected.payload
            ):
                raise ValueError("interpreter proposal differs from the canonical v2 command")
            event = GameEvent(
                event_id=event_id,
                idempotency_key=event_id,
                parent_event_id=identities[-2] if len(identities) > 1 else None,
                actor_id=turn.actor_id,
                kind=proposed.kind,
                payload=payload.model_dump(),
            )
            self._ensure_world(world_id, spec, turn.scenario)
            # Racing requests may move a head, create the fork or commit this action first.
            # Every commit is guarded in its own transaction, so reselect instead of guessing.
            for attempt in range(3):
                saved = self._read_prefix(world_id, identities)
                if saved is not None:
                    break
                forked = False
                try:
                    branch_id, forked = self._branch_for(world_id, identities)
                    outcome = self.repository.dispatch(
                        world_id,
                        event,
                        branch_id=branch_id,
                        expected_seq=len(identities) - 1,
                        unique_in_world=True,
                    )
                except (RepositoryConflict, RepositoryNotFound):
                    # Not found here means a racing request discarded the empty fork this
                    # one had selected; reselecting recreates it. The world itself exists.
                    if attempt == 2:
                        raise
                    continue
                except ValueError:
                    # The rules rejected the edited action before any commit; do not leave
                    # the fork made for it behind. A racing identical request that already
                    # used the branch keeps it, because discard refuses committed branches.
                    if forked:
                        with contextlib.suppress(RepositoryConflict, RepositoryNotFound):
                            self.repository.discard_empty_branch(world_id, branch_id)
                    raise
                saved, replayed = (branch_id, outcome.entry), outcome.replayed
                break
        branch_id, saved = saved

        if not replayed:
            await self._remember(world_id, branch_id, saved)
        memories = (
            await self._recall(world_id, branch_id, turn.actions[-1].text)
            if self._is_head(world_id, branch_id, saved)
            else []
        )
        return CommittedTurn(
            schema_version=turn.schema_version,
            identity_version=identity_version,
            legacy_read_only=legacy_exists,
            world_id=world_id,
            branch_id=branch_id,
            event_id=event_id,
            replayed=replayed,
            entry=saved,
            spec=spec,
            display=render_display(spec, saved.result.state, viewer=turn.actor_id),
            scene=scene_text(saved.result.state, turn.actor_id),
            memories=memories,
        )

    async def adjudicate(
        self,
        request: TurnRequest,
        *,
        vertex_credentials: SecretStr | None = None,
        llm_api_key: SecretStr | None = None,
    ) -> dict:
        """Hook-mode turn commit: returns authoritative result and read-only prompt block."""
        vertex_narrator = (
            VertexNarrator(vertex_credentials) if vertex_credentials is not None else None
        )
        intent_tool: OpenAICompatIntentTool | None = None
        if request.intent_llm is not None:
            _validate_intent_llm_url(request.intent_llm.base_url)
            resolved_key = (
                llm_api_key.get_secret_value().strip()
                if llm_api_key is not None and llm_api_key.get_secret_value().strip()
                else (
                    self.openai_intent.config.api_key.get_secret_value()
                    if self.openai_intent is not None
                    and self.openai_intent.config.api_key is not None
                    else None
                )
            )
            cfg = OpenAICompatConfig.from_values(
                base_url=request.intent_llm.base_url,
                narrator_model=request.intent_llm.model,
                intent_model=request.intent_llm.model,
                api_key=resolved_key,
                timeout_seconds=request.intent_llm.timeout_seconds,
                reasoning_effort=request.intent_llm.reasoning_effort,
                custom_headers=request.intent_llm.custom_headers,
                custom_body=request.intent_llm.custom_body,
            )
            intent_tool = OpenAICompatIntentTool(cfg)
        committed = await self.commit_turn(
            request.saga,
            vertex_narrator=vertex_narrator,
            client_proposal=request.client_proposal,
            intent_tool=intent_tool,
        )
        return {"saga": committed.saga_payload(include_prompt_block=True)}

    async def complete(
        self, request: CompletionRequest, *, vertex_credentials: SecretStr | None = None
    ) -> dict:
        if (request.narration is None) != (vertex_credentials is None):
            raise ValueError(
                "Vertex narration requires its configuration and service-account JSON."
            )
        vertex_narrator = (
            VertexNarrator(vertex_credentials) if vertex_credentials is not None else None
        )
        narrator = vertex_narrator if vertex_narrator is not None else self.narrator
        committed = await self.commit_turn(request.saga, vertex_narrator=vertex_narrator)
        saved = committed.entry
        narration = NarrationRequest(
            world_id=committed.world_id,
            event=saved.committed.event.model_copy(deep=True),
            result=saved.result.model_copy(deep=True),
            memories=committed.memories,
            recent_dialogue=request.narration.context if request.narration else [],
            risu_prompt=[msg.model_dump() for msg in request.risu_prompt],
            scene=committed.scene,
        )
        try:
            prose = await narrator.render(narration)
            if not isinstance(prose, str) or not prose.strip():
                raise ValueError("empty narrator response")
        except Exception:
            logger.warning("Risu narrator failed after commit; using recorded facts")
            prose = await FallbackNarrator().render(
                NarrationRequest(
                    world_id=committed.world_id,
                    event=saved.committed.event.model_copy(deep=True),
                    result=saved.result.model_copy(deep=True),
                )
            )
            if request.narration:
                prose += "\n\n모델 서술에 실패하여 확정된 기본 서술을 표시합니다."
        return {
            "id": "chatcmpl-" + committed.event_id,
            "object": "chat.completion",
            "created": 0,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": prose},
                    "finish_reason": "stop",
                }
            ],
            "saga": committed.saga_payload(),
        }

    async def prepare_stream(
        self, request: CompletionRequest, *, vertex_credentials: SecretStr | None = None
    ) -> AsyncIterator[str]:
        """Commit first, then return an SSE stream of OpenAI-compatible chunks plus final saga."""
        if (request.narration is None) != (vertex_credentials is None):
            raise ValueError(
                "Vertex narration requires its configuration and service-account JSON."
            )
        vertex_narrator = (
            VertexNarrator(vertex_credentials) if vertex_credentials is not None else None
        )
        narrator = vertex_narrator if vertex_narrator is not None else self.narrator
        committed = await self.commit_turn(request.saga, vertex_narrator=vertex_narrator)
        saved = committed.entry
        narration = NarrationRequest(
            world_id=committed.world_id,
            event=saved.committed.event.model_copy(deep=True),
            result=saved.result.model_copy(deep=True),
            memories=committed.memories,
            recent_dialogue=request.narration.context if request.narration else [],
            risu_prompt=[msg.model_dump() for msg in request.risu_prompt],
            scene=committed.scene,
        )
        chunk_id = "chatcmpl-" + committed.event_id

        def _chunk(
            delta: dict[str, str], *, finish_reason: str | None = None, saga: dict | None = None
        ) -> str:
            payload: dict[str, Any] = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": 0,
                "model": request.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            if saga is not None:
                payload["saga"] = saga
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        async def _generate() -> AsyncIterator[str]:
            yielded = False
            try:
                if isinstance(narrator, OpenAICompatNarrator):
                    async for piece in narrator.stream(narration):
                        yielded = True
                        yield _chunk({"content": piece})
                else:
                    prose = await narrator.render(narration)
                    if not isinstance(prose, str) or not prose.strip():
                        raise ValueError("empty narrator response")
                    yielded = True
                    yield _chunk({"content": prose})
            except Exception:
                logger.warning("Risu streaming narrator failed after commit; using recorded facts")
                if not yielded:
                    prose = await FallbackNarrator().render(
                        NarrationRequest(
                            world_id=committed.world_id,
                            event=saved.committed.event.model_copy(deep=True),
                            result=saved.result.model_copy(deep=True),
                        )
                    )
                    yield _chunk({"content": prose})
            yield _chunk({}, finish_reason="stop", saga=committed.saga_payload())
            yield "data: [DONE]\n\n"

        return _generate()


def _decode_vertex_secret(vertex_secret: SecretStr | None) -> SecretStr | None:
    if vertex_secret is None:
        return None
    try:
        encoded = vertex_secret.get_secret_value()
        if not 1 <= len(encoded) <= 24000:
            raise ValueError
        return SecretStr(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except Exception:
        raise ValueError("Vertex 서비스 계정 JSON을 확인하세요.") from None


def router(bridge: RisuBridge) -> APIRouter:
    routes = APIRouter()

    @routes.post("/v1/saga/intent-context")
    async def intent_context(request: IntentContextRequest) -> dict:
        return bridge.prepare_intent_context(request.saga)

    @routes.post("/v1/saga/turn")
    async def adjudicate_turn(
        request: TurnRequest,
        vertex_secret: Annotated[SecretStr | None, Header(alias="x-saga-vertex-secret")] = None,
        llm_api_key: Annotated[SecretStr | None, Header(alias="x-saga-llm-api-key")] = None,
    ) -> dict:
        return await bridge.adjudicate(
            request,
            vertex_credentials=_decode_vertex_secret(vertex_secret),
            llm_api_key=llm_api_key,
        )

    @routes.post("/v1/chat/completions", response_model=None)
    async def complete(
        request: CompletionRequest,
        vertex_secret: Annotated[SecretStr | None, Header(alias="x-saga-vertex-secret")] = None,
    ) -> dict | StreamingResponse:
        creds = _decode_vertex_secret(vertex_secret)
        if request.stream:
            stream = await bridge.prepare_stream(request, vertex_credentials=creds)
            return StreamingResponse(
                stream,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store"},
            )
        return await bridge.complete(request, vertex_credentials=creds)

    @routes.get("/v1/models")
    async def models() -> dict:
        return {
            "object": "list",
            "data": [{"id": "saga-srd-demo", "object": "model", "created": 0, "owned_by": "saga"}],
        }

    return routes
