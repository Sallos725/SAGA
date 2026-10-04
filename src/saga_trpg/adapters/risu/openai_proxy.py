"""Versioned, no-memory PocketRisu bridge; all effects go through the repository."""

from __future__ import annotations

import logging
import re
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from saga_trpg.adapters.narrator.fallback import FallbackNarrator
from saga_trpg.adapters.storage.sqlite import (
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWorldRepository,
)
from saga_trpg.domain.events import GameEvent
from saga_trpg.modules.srd_5_2_1.rules import SRD521Rules
from saga_trpg.ports.intent import IntentPort
from saga_trpg.ports.narrator import NarrationRequest, NarratorPort
from saga_trpg.runtime.hashing import stable_value_hash
from saga_trpg.runtime.journal import JournalEntry

logger = logging.getLogger(__name__)
Identity = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HostAction(ClosedModel):
    message_id: Identity
    text: str = Field(min_length=1, max_length=2048)


class HostTurn(ClosedModel):
    schema_version: Literal[1]
    host_instance_id: Identity
    chat_id: Identity
    actor_id: Literal["player"]
    purpose: Literal["action"]
    actions: list[HostAction] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def distinct_messages(self) -> HostTurn:
        ids = [action.message_id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate host user-message identity")
        return self


class ChatMessage(ClosedModel):
    role: Literal["system", "user", "assistant", "developer"]
    content: str = Field(max_length=32000)


class CompletionRequest(ClosedModel):
    model: Literal["saga-srd-demo"]
    messages: list[ChatMessage] = Field(min_length=1, max_length=512)
    stream: Literal[False] = False
    saga: HostTurn

    @model_validator(mode="after")
    def matching_action(self) -> CompletionRequest:
        latest = self.messages[-1]
        if latest.role != "user" or latest.content != self.saga.actions[-1].text:
            raise ValueError("last prompt message must match the identified user action exactly")
        return self


class CommandInterpreter:
    """Closed SRD demo commands, deliberately independent of generated prose."""

    async def interpret(self, *, world_id: str, actor_id: str, text: str) -> GameEvent:
        words = text.strip().lower().split()
        circumstances = {}
        if words and words[-1] in {"advantage", "disadvantage"}:
            circumstances[words.pop()] = True
        if len(words) == 2 and words[0] == "attack":
            kind, payload = "attack", {"target_id": words[1], **circumstances}
        elif (
            len(words) == 3
            and words[0] in {"check", "save"}
            and re.fullmatch(r"[0-9]{1,3}", words[2])
        ):
            kind = "ability_check" if words[0] == "check" else "saving_throw"
            payload = {"ability": words[1], "difficulty": int(words[2]), **circumstances}
        else:
            raise ValueError(
                "Supported commands: attack opponent; check strength 10; save dexterity 12. "
                "Optionally append advantage or disadvantage. No natural-language intents yet."
            )
        validated = SRD521Rules.payload_models[kind].model_validate(payload)
        return GameEvent(kind=kind, actor_id=actor_id, payload=validated.model_dump())


class RisuBridge:
    def __init__(
        self,
        repository: SQLiteWorldRepository,
        *,
        interpreter: IntentPort | None = None,
        narrator: NarratorPort | None = None,
    ) -> None:
        self.repository = repository
        self.interpreter = interpreter or CommandInterpreter()
        self.narrator = narrator or FallbackNarrator()

    @staticmethod
    def identities(turn: HostTurn) -> tuple[str, list[str]]:
        world_id = "risu-" + stable_value_hash(
            ["saga-risu-v1", turn.host_instance_id, turn.chat_id]
        )
        identities = []
        parent = None
        for action in turn.actions:
            parent = "risu-" + stable_value_hash(
                ["saga-risu-action-v1", world_id, parent, action.message_id, action.text]
            )
            identities.append(parent)
        return world_id, identities

    def _read_prefix(self, world_id: str, identities: list[str]) -> JournalEntry | None:
        if not identities:
            return None
        try:
            entries = self.repository.entries(world_id, branch_id=identities[-1])
        except RepositoryNotFound:
            return None
        saved_ids = [entry.committed.event.event_id for entry in entries]
        # A crashed pre-dispatch fork may contain only the parent's prefix.
        if saved_ids == identities[:-1]:
            return None
        if saved_ids != identities:
            raise RepositoryConflict("SAGA branch history conflicts with host action identities")
        return entries[-1]

    async def complete(self, request: CompletionRequest) -> dict:
        turn = request.saga
        world_id, identities = self.identities(turn)
        event_id = branch_id = identities[-1]
        saved = self._read_prefix(world_id, identities)
        replayed = saved is not None
        if saved is None:
            if len(identities) > 1 and self._read_prefix(world_id, identities[:-1]) is None:
                raise RepositoryConflict(
                    "Uncommitted or edited earlier history. Rewind the host chat to the first "
                    "changed user turn and generate there before adding later actions."
                )
            proposed = await self.interpreter.interpret(
                world_id=world_id, actor_id=turn.actor_id, text=turn.actions[-1].text
            )
            proposed = GameEvent.model_validate(proposed.model_dump())
            payload_model = SRD521Rules.payload_models.get(proposed.kind)
            if payload_model is None or proposed.actor_id != turn.actor_id:
                raise ValueError("intent must select a supported action for the identified actor")
            payload = payload_model.model_validate(proposed.payload)
            event = GameEvent(
                event_id=event_id,
                idempotency_key=event_id,
                parent_event_id=identities[-2] if len(identities) > 1 else None,
                actor_id=turn.actor_id,
                kind=proposed.kind,
                payload=payload.model_dump(),
            )
            definition = self.repository.registry.get("srd-5.2.1", "0.1.0")
            self.repository.create_world(
                definition.example_world(world_id),
                if_missing=True,
                ruleset_id="srd-5.2.1",
                ruleset_version="0.1.0",
            )
            selection = self.repository.ruleset_for_world(world_id)
            if (selection.ruleset_id, selection.ruleset_version) != ("srd-5.2.1", "0.1.0"):
                raise RepositoryConflict("host world has an incompatible ruleset")
            source = identities[-2] if len(identities) > 1 else "main"
            try:
                self.repository.fork(
                    world_id,
                    branch_id=branch_id,
                    source_branch_id=source,
                    at_event_id=event.parent_event_id,
                )
            except RepositoryConflict:
                branch = next(
                    (b for b in self.repository.branches(world_id) if b.branch_id == branch_id),
                    None,
                )
                if (
                    branch is None
                    or branch.parent_branch_id != source
                    or branch.fork_event_id != event.parent_event_id
                ):
                    raise
            # Verify an existing/crash-recovered fork before any dispatch.
            saved = self._read_prefix(world_id, identities)
            if saved is None:
                outcome = self.repository.dispatch(world_id, event, branch_id=branch_id)
                saved, replayed = outcome.entry, outcome.replayed
            else:
                replayed = True

        narration = NarrationRequest(
            world_id=world_id,
            event=saved.committed.event.model_copy(deep=True),
            result=saved.result.model_copy(deep=True),
        )
        try:
            prose = await self.narrator.render(narration)
            if not isinstance(prose, str) or not prose.strip():
                raise ValueError("empty narrator response")
        except Exception:
            logger.warning("Risu narrator failed after commit; using recorded facts", exc_info=True)
            prose = await FallbackNarrator().render(
                NarrationRequest(
                    world_id=world_id,
                    event=saved.committed.event.model_copy(deep=True),
                    result=saved.result.model_copy(deep=True),
                )
            )
        result = saved.result.model_dump(mode="json")
        return {
            "id": "chatcmpl-" + event_id,
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
            "saga": {
                "schema_version": 1,
                "world_id": world_id,
                "branch_id": branch_id,
                "event_id": event_id,
                "seq": saved.committed.seq,
                "replayed": replayed,
                **result,
            },
        }


def router(bridge: RisuBridge) -> APIRouter:
    routes = APIRouter()

    @routes.post("/v1/chat/completions")
    async def complete(request: CompletionRequest) -> dict:
        return await bridge.complete(request)

    @routes.get("/v1/models")
    async def models() -> dict:
        return {
            "object": "list",
            "data": [{"id": "saga-srd-demo", "object": "model", "created": 0, "owned_by": "saga"}],
        }

    return routes
