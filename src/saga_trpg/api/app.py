from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from saga_trpg.adapters.risu.openai_proxy import RisuBridge
from saga_trpg.adapters.risu.openai_proxy import router as risu_router
from saga_trpg.adapters.storage.sqlite import (
    RepositoryConflict,
    RepositoryNotFound,
    RepositoryVersionError,
    SQLiteWorldRepository,
)
from saga_trpg.domain.branches import BranchInfo, ForkRequest
from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.domain.rulesets import CreateWorldRequest, RulesetContext, RulesetManifest
from saga_trpg.modules.catalog import COMMON_BOT_DESCRIPTION
from saga_trpg.ports.tools import ToolExecutionPending, ToolIdentityConflict
from saga_trpg.rulebooks.loader import RulebookError
from saga_trpg.rulebooks.risu_module import sha256_text
from saga_trpg.runtime.engine import IdempotencyConflict
from saga_trpg.runtime.journal import JournalEntry
from saga_trpg.runtime.replay import ReplayError

logger = logging.getLogger(__name__)


class RulebookImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=2, max_length=2_000_000)


class RulebookImported(BaseModel):
    ruleset_id: str
    ruleset_version: str
    sha256: str
    created: bool


def create_app(
    *, repository: SQLiteWorldRepository | None = None, risu_bridge: RisuBridge | None = None
) -> FastAPI:
    app = FastAPI(title="SAGA — Stateful Agentic Game Architecture", version="0.2.0")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # FastAPI's default error includes input values, including malformed credentials.
        if request.url.path == "/v1/chat/completions":
            return JSONResponse(
                status_code=422,
                content={"detail": "SAGA 요청의 식별자·명령·Vertex 설정 형식을 확인하세요."},
            )
        return await request_validation_exception_handler(request, exc)

    worlds = (
        repository
        if repository is not None
        else SQLiteWorldRepository(
            Path(os.getenv("TRPG_DATA_DIR", "./data")) / "worlds.sqlite3",
            seed=os.getenv("TRPG_DEFAULT_SEED", "dev-seed"),
        )
    )

    def ensure_demo_world(world_id: str, branch_id: str) -> None:
        if branch_id == "main":
            worlds.create_world(
                WorldState(
                    world_id=world_id,
                    actors={
                        "player": ActorState(actor_id="player", hp=20, max_hp=20),
                        "goblin": ActorState(actor_id="goblin", hp=12, max_hp=12),
                    },
                ),
                if_missing=True,
            )

    @app.exception_handler(IdempotencyConflict)
    @app.exception_handler(ToolIdentityConflict)
    @app.exception_handler(RepositoryConflict)
    async def conflict(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RepositoryNotFound)
    async def not_found(request: Request, exc: RepositoryNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ToolExecutionPending)
    async def pending_tool(request: Request, exc: ToolExecutionPending) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ReplayError)
    @app.exception_handler(RepositoryVersionError)
    @app.exception_handler(sqlite3.DatabaseError)
    async def storage_failure(request: Request, exc: Exception) -> JSONResponse:
        logger.error("world storage operation failed", exc_info=exc)
        return JSONResponse(status_code=503, content={"detail": "world storage unavailable"})

    @app.exception_handler(ValueError)
    async def invalid_event(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/rulesets")
    def list_rulesets() -> list[RulesetManifest]:
        return worlds.registry.manifests()

    @app.get("/rulesets/{ruleset_id}")
    def describe_ruleset(
        ruleset_id: str, version: str = Query("0.1.0", min_length=1)
    ) -> RulesetManifest:
        return worlds.registry.get(ruleset_id, version).manifest.model_copy(deep=True)

    @app.get("/rulebooks")
    def list_rulebooks() -> list[RulesetManifest]:
        return worlds.registry.manifests()

    @app.get("/rulebooks/{ruleset_id}")
    def describe_rulebook(
        ruleset_id: str, version: str = Query(..., min_length=1)
    ) -> RulesetManifest:
        return worlds.registry.get(ruleset_id, version).manifest.model_copy(deep=True)

    @app.post("/rulebooks", status_code=201)
    def import_rulebook(request: RulebookImport) -> RulebookImported:
        """Validate and register a rulebook (for example one carried by a RisuAI module)."""
        try:
            manifest, created = worlds.register_rulebook(request.content)
        except RulebookError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RulebookImported(
            ruleset_id=manifest.ruleset_id,
            ruleset_version=manifest.ruleset_version,
            sha256=sha256_text(request.content),
            created=created,
        )

    @app.post("/worlds/{world_id}", status_code=201)
    def create_world(world_id: str, request: CreateWorldRequest) -> WorldState:
        definition = worlds.registry.get(request.ruleset_id, request.ruleset_version)
        initial = definition.example_world(world_id)
        worlds.create_world(
            initial,
            ruleset_id=request.ruleset_id,
            ruleset_version=request.ruleset_version,
        )
        return initial

    @app.get("/worlds/{world_id}/ruleset")
    def world_ruleset(world_id: str) -> RulesetContext:
        selected = worlds.ruleset_for_world(world_id)
        return RulesetContext(
            common_bot_description=COMMON_BOT_DESCRIPTION,
            manifest=worlds.registry.get(selected.ruleset_id, selected.ruleset_version).manifest,
        )

    @app.get("/worlds/{world_id}/state")
    def world_state(world_id: str, branch_id: str = Query("main", min_length=1)) -> WorldState:
        ensure_demo_world(world_id, branch_id)
        return worlds.get_state(world_id, branch_id=branch_id)

    @app.post("/worlds/{world_id}/events")
    def dispatch_event(
        world_id: str, event: GameEvent, branch_id: str = Query("main", min_length=1)
    ):
        ensure_demo_world(world_id, branch_id)
        try:
            outcome = worlds.dispatch(world_id, event, branch_id=branch_id)
        except (KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="invalid event payload") from exc
        return {
            "replayed": outcome.replayed,
            "event_id": outcome.entry.committed.event.event_id,
            "seq": outcome.entry.committed.seq,
            "facts": outcome.entry.result.facts,
            "rolls": outcome.entry.result.rolls,
            "patches": outcome.entry.result.patches,
            "state": outcome.entry.result.state,
            "resolution": outcome.entry.result.resolution,
        }

    @app.get("/worlds/{world_id}/events/{event_id}")
    def get_event(
        world_id: str, event_id: str, branch_id: str = Query("main", min_length=1)
    ) -> JournalEntry:
        entry = worlds.get_event(world_id, event_id, branch_id=branch_id)
        if not entry:
            raise HTTPException(status_code=404, detail="event not found")
        return entry

    @app.get("/worlds/{world_id}/events")
    def list_events(
        world_id: str, branch_id: str = Query("main", min_length=1)
    ) -> list[JournalEntry]:
        return worlds.entries(world_id, branch_id=branch_id)

    @app.get("/worlds/{world_id}/replay")
    def replay(
        world_id: str,
        branch_id: str = Query("main", min_length=1),
        at_event_id: str | None = Query(None, min_length=1),
    ) -> WorldState:
        return worlds.replay(world_id, branch_id=branch_id, at_event_id=at_event_id)

    @app.get("/worlds/{world_id}/branches")
    def list_branches(world_id: str) -> list[BranchInfo]:
        return worlds.branches(world_id)

    @app.post("/worlds/{world_id}/branches", status_code=201)
    def fork(world_id: str, request: ForkRequest) -> BranchInfo:
        return worlds.fork(world_id, **request.model_dump())

    if risu_bridge is None:
        memory = None
        nmos_url = os.getenv("SAGA_NMOS_URL", "").strip()
        if nmos_url:
            from saga_trpg.adapters.memory.nmos import NMOSAdapter

            memory = NMOSAdapter(
                base_url=nmos_url,
                api_key=os.getenv("SAGA_NMOS_API_KEY") or None,
                timeout_seconds=float(os.getenv("SAGA_NMOS_TIMEOUT_SECONDS", "2")),
            )
        risu_bridge = RisuBridge(worlds, memory=memory)
    app.include_router(risu_router(risu_bridge))
    return app
