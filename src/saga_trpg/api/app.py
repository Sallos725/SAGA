from __future__ import annotations

import base64
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from saga_trpg.adapters.narrator.openai_compat import OpenAICompatConfig
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
from saga_trpg.rulebooks.converter import (
    MAX_UPLOAD_BYTES,
    ConversionError,
    card_to_scenario_draft,
    enrich_card_with_llm,
    extract_card_from_bytes,
    extract_card_from_dict,
)
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.rulebooks.loader import RulebookError
from saga_trpg.rulebooks.risu_module import sha256_text
from saga_trpg.rulebooks.scenarios import build_scenario_world, parse_scenario
from saga_trpg.runtime.engine import IdempotencyConflict
from saga_trpg.runtime.journal import JournalEntry
from saga_trpg.runtime.replay import ReplayError

logger = logging.getLogger(__name__)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_BLOCKED_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal", "metadata.internal"})


class RulebookImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=2, max_length=2_000_000)


class RulebookImported(BaseModel):
    ruleset_id: str
    ruleset_version: str
    sha256: str
    created: bool


class LlmConvertOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=8, max_length=512)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    reasoning_effort: str | None = Field(default=None, max_length=20)
    custom_headers: dict[str, str] | None = None
    custom_body: dict[str, Any] | None = None
    timeout_seconds: float = Field(default=45.0, gt=0, le=180)


class CardConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card: dict[str, Any] | None = None
    file_base64: str | None = Field(default=None, max_length=22_000_000)
    filename: str = Field(default="", max_length=255)
    extra_lorebook: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    ruleset_id: str | None = Field(default=None, min_length=1, max_length=80)
    ruleset_version: str | None = Field(default=None, min_length=1, max_length=40)
    use_llm: bool = False
    llm: LlmConvertOverride | None = None


def _validate_client_llm_url(url: str) -> None:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTS:
        raise ValueError("disallowed LLM endpoint host")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in _LOOPBACK_HOSTS:
        return
    raise ValueError("client-provided LLM base_url must use https:// or localhost http://")


def _converter_html_path() -> Path | None:
    candidates = [
        Path.cwd() / "tools" / "saga-converter.html",
        Path(__file__).resolve().parents[3] / "tools" / "saga-converter.html",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def create_app(
    *, repository: SQLiteWorldRepository | None = None, risu_bridge: RisuBridge | None = None
) -> FastAPI:
    app = FastAPI(title="SAGA — Stateful Agentic Game Architecture", version="0.2.0")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # FastAPI's default error includes input values, including malformed credentials.
        if request.url.path in ("/v1/chat/completions", "/v1/saga/turn", "/v1/saga/convert"):
            return JSONResponse(
                status_code=422,
                content={"detail": "SAGA 요청의 식별자·명령·설정 형식을 확인하세요."},
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
        if request.scenario is not None:
            rulebook = definition.rules.spec if isinstance(definition.rules, DataRules) else None
            initial = build_scenario_world(
                parse_scenario(request.scenario), world_id, rulebook=rulebook
            )
        else:
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

    server_llm_config: OpenAICompatConfig | None = None
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
        narrator = None
        openai_intent = None
        llm_base_url = os.getenv("SAGA_LLM_BASE_URL", "").strip()
        narrator_model = os.getenv("SAGA_NARRATOR_MODEL", "").strip()
        if llm_base_url and narrator_model:
            from saga_trpg.adapters.intent.openai_compat import OpenAICompatIntentTool
            from saga_trpg.adapters.narrator.openai_compat import OpenAICompatNarrator

            intent_model = os.getenv("SAGA_INTENT_MODEL", "").strip() or None
            server_llm_config = OpenAICompatConfig.from_values(
                base_url=llm_base_url,
                narrator_model=narrator_model,
                api_key=os.getenv("SAGA_LLM_API_KEY") or None,
                intent_model=intent_model,
                timeout_seconds=float(os.getenv("SAGA_LLM_TIMEOUT_SECONDS", "45")),
                reasoning_effort=os.getenv("SAGA_LLM_REASONING_EFFORT") or None,
                custom_headers=os.getenv("SAGA_LLM_CUSTOM_HEADERS") or None,
                custom_body=os.getenv("SAGA_LLM_CUSTOM_BODY") or None,
            )
            narrator = OpenAICompatNarrator(server_llm_config)
            if intent_model or os.getenv("SAGA_OPENAI_INTENT", "").strip() in ("1", "true", "yes"):
                openai_intent = OpenAICompatIntentTool(server_llm_config)
        risu_bridge = RisuBridge(
            worlds, narrator=narrator, memory=memory, openai_intent=openai_intent
        )

    @app.post("/v1/saga/convert")
    async def convert_card(request: CardConvertRequest) -> dict[str, Any]:
        """A2: convert a RisuAI card (.charx/.png/.json or character dict) into a scenario draft."""
        if (request.card is None) == (request.file_base64 is None):
            raise HTTPException(
                status_code=422, detail="provide exactly one of 'card' or 'file_base64'"
            )
        rulebook = None
        if request.ruleset_id is not None:
            version = request.ruleset_version
            if version is None:
                manifests = [
                    m for m in worlds.registry.manifests() if m.ruleset_id == request.ruleset_id
                ]
                if not manifests:
                    raise HTTPException(status_code=404, detail="unknown ruleset_id")
                version = manifests[-1].ruleset_version
            definition = worlds.registry.get(request.ruleset_id, version)
            if isinstance(definition.rules, DataRules):
                rulebook = definition.rules.spec
        try:
            if request.file_base64 is not None:
                try:
                    raw_bytes = base64.b64decode(request.file_base64, validate=True)
                except ValueError:
                    raise ConversionError("file_base64 is not valid base64") from None
                if len(raw_bytes) > MAX_UPLOAD_BYTES:
                    raise ConversionError("uploaded card exceeds maximum size (16 MB)")
                parsed = extract_card_from_bytes(
                    raw_bytes, filename=request.filename, extra_lorebook=request.extra_lorebook
                )
            else:
                assert request.card is not None
                parsed = extract_card_from_dict(request.card, extra_lorebook=request.extra_lorebook)
            if request.use_llm:
                if request.llm is not None:
                    _validate_client_llm_url(request.llm.base_url)
                    cfg = OpenAICompatConfig.from_values(
                        base_url=request.llm.base_url,
                        narrator_model=request.llm.model,
                        intent_model=request.llm.model,
                        api_key=(
                            request.llm.api_key.get_secret_value()
                            if request.llm.api_key is not None
                            else None
                        ),
                        timeout_seconds=request.llm.timeout_seconds,
                        reasoning_effort=request.llm.reasoning_effort,
                        custom_headers=request.llm.custom_headers,
                        custom_body=request.llm.custom_body,
                    )
                elif server_llm_config is not None:
                    cfg = server_llm_config
                else:
                    raise ConversionError(
                        "use_llm=true requires either request.llm settings or server SAGA_LLM_*"
                    )
                result = await enrich_card_with_llm(parsed, cfg, rulebook=rulebook)
            else:
                result = card_to_scenario_draft(parsed, rulebook=rulebook)
        except (ConversionError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except httpx.HTTPError:
            raise HTTPException(
                status_code=502, detail="upstream LLM provider request failed"
            ) from None
        return result.model_dump(mode="json")

    @app.get("/converter")
    async def converter_webapp() -> HTMLResponse:
        """Serve the standalone single-HTML A2 Card → SAGA Scenario Converter with strict CSP."""
        html_path = _converter_html_path()
        if html_path is None:
            raise HTTPException(status_code=404, detail="converter webapp not found")
        nonce = secrets.token_urlsafe(16)
        raw_html = html_path.read_text(encoding="utf-8")
        # Inject per-request nonce so CSP needs no 'unsafe-inline' when served over HTTP.
        rendered = raw_html.replace("<style>", f'<style nonce="{nonce}">', 1).replace(
            "<script>", f'<script nonce="{nonce}">', 1
        )
        csp = (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; "
            f"style-src 'nonce-{nonce}'; "
            "connect-src 'self' https: http://127.0.0.1:* http://localhost:*; "
            "img-src 'self' data: blob:; "
            "form-action 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        )
        return HTMLResponse(
            content=rendered,
            headers={
                "Content-Security-Policy": csp,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            },
        )

    app.include_router(risu_router(risu_bridge))
    return app
