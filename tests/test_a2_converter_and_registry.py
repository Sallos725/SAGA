"""A2 invariants: card conversion, dynamic actor/room registry, and LLM custom params."""

from __future__ import annotations

import base64
import io
import json
import struct
import zipfile
import zlib

import httpx
import pytest
from fastapi.testclient import TestClient
from test_risu_api import setup

from saga_trpg.adapters.narrator.openai_compat import OpenAICompatConfig
from saga_trpg.api.app import create_app
from saga_trpg.domain.events import (
    ADD_ROOM_KIND,
    REGISTER_ACTOR_KIND,
)
from saga_trpg.domain.models import ActorState
from saga_trpg.rulebooks.__main__ import main as cli_main
from saga_trpg.rulebooks.charx import build_card_v3, placeholder_icon, write_charx
from saga_trpg.rulebooks.converter import (
    ConversionError,
    card_to_scenario_draft,
    enrich_card_with_llm,
    extract_card_from_bytes,
    extract_card_from_dict,
)
from saga_trpg.runtime.hashing import stable_model_hash


def _make_png_with_card(
    card: dict, *, chunk_type: bytes = b"tEXt", keyword: bytes = b"chara"
) -> bytes:
    base_png = placeholder_icon()
    payload_b64 = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    if chunk_type == b"tEXt":
        body = keyword + b"\x00" + payload_b64
    elif chunk_type == b"zTXt":
        body = keyword + b"\x00\x00" + zlib.compress(payload_b64)
    else:
        raise ValueError(chunk_type)
    chunk = (
        struct.pack(">I", len(body))
        + chunk_type
        + body
        + struct.pack(">I", zlib.crc32(chunk_type + body) & 0xFFFFFFFF)
    )
    # Insert right before IEND (last 12 bytes)
    return base_png[:-12] + chunk + base_png[-12:]


SAMPLE_CARD = {
    "spec": "chara_card_v3",
    "spec_version": "3.0",
    "data": {
        "name": "엘레나 폰 크로이츠",
        "description": "폐허가 된 수도원을 지키는 은발의 기사. HP: 28/28",
        "personality": "과묵하지만 약속을 지킨다.",
        "scenario": "안개 낀 밤, 오래된 수도원 정문 앞에서 엘레나와 마주친다.",
        "first_mes": "이곳은 출입이 금지되어 있습니다.",
        "tags": ["fantasy", "knight"],
        "character_book": {
            "entries": [
                {
                    "comment": "NPC: 상인 마르코",
                    "keys": ["마르코", "Marco"],
                    "content": "수도원 근처를 떠도는 약초 상인.",
                },
                {
                    "comment": "장소: 수도원 지하 서고",
                    "keys": ["지하 서고", "서고"],
                    "content": "먼지 쌓인 고서와 낡은 제단이 놓인 지하 공간.",
                },
                {
                    "comment": "saga:rulebook:srd@0.4.0:deadbeef",
                    "keys": ["(?!)"],
                    "content": "ignored internal saga metadata",
                },
            ]
        },
    },
}


# --- 1. Hash neutrality & A2 dynamic registration (register_actor / add_room) ----------------


def test_actor_state_a2_metadata_is_hash_neutral_when_default():
    plain = ActorState(actor_id="player", hp=20, max_hp=20)
    dumped = plain.model_dump(mode="json")
    assert "display_name" not in dumped
    assert "aliases" not in dumped
    assert "provenance" not in dumped
    assert stable_model_hash(plain) == stable_model_hash(
        ActorState.model_validate_json(plain.model_dump_json())
    )


def test_register_actor_alias_dedup_and_add_room_in_play(tmp_path):
    repo, dice, intent, client = setup(tmp_path)

    def hook(actions, *, proposal=None):
        body: dict = {
            "saga": {
                "schema_version": 4,
                "host_instance_id": "host-a2",
                "chat_id": "chat-a2-dyn",
                "purpose": "action",
                "actor_id": "player",
                "actions": [{"message_id": mid, "text": txt} for mid, txt in actions],
            }
        }
        if proposal is not None:
            body["client_proposal"] = proposal
        return client.post("/v1/saga/turn", json=body)

    # 1. Start chat and register a new NPC with display_name + aliases + provenance
    a1 = [("u1", "대장장이 바론을 만난다")]
    r1 = hook(
        a1,
        proposal={
            "kind": REGISTER_ACTOR_KIND,
            "payload": {
                "actor_id": "baron_smith",
                "display_name": "대장장이 바론",
                "aliases": ["바론", "Baron"],
                "hp": 22,
                "max_hp": 22,
                "provenance": {"display_name": "card", "hp": "inferred"},
            },
        },
    )
    assert r1.status_code == 200, r1.text
    s1 = r1.json()["saga"]
    assert s1["state"]["actors"]["baron_smith"]["display_name"] == "대장장이 바론"
    assert s1["state"]["actors"]["baron_smith"]["aliases"] == ["바론", "Baron"]
    assert s1["state"]["actors"]["baron_smith"]["provenance"]["display_name"] == "card"
    assert any(
        p["actor_id"] == "baron_smith" and p["display_name"] == "대장장이 바론"
        for p in s1["scene_board"]["participants"]
    )

    # 2. Attempting to register the same NPC under another ID with conflicting alias fails (422)!
    dup = hook(
        a1 + [("u2", "바론을 또 등록한다")],
        proposal={
            "kind": REGISTER_ACTOR_KIND,
            "payload": {
                "actor_id": "smith_duplicate",
                "display_name": "Baron",
                "hp": 18,
                "max_hp": 18,
            },
        },
    )
    assert dup.status_code == 422
    assert "alias/name conflict" in dup.json()["detail"]

    # 3. Explicitly allowing a distinct instance (e.g., second guard) succeeds!
    a2 = a1 + [("u2", "두 번째 경비를 등록한다")]
    r2 = hook(
        a2,
        proposal={
            "kind": REGISTER_ACTOR_KIND,
            "payload": {
                "actor_id": "baron_twin",
                "display_name": "Baron",
                "hp": 18,
                "max_hp": 18,
                "allow_distinct_instance": True,
            },
        },
    )
    assert r2.status_code == 200, r2.text

    # 4. Dynamically add a connected room via add_room and move into it!
    a3 = a2 + [("u3", "숨겨진 대장간 문을 발견한다")]
    r3 = hook(
        a3,
        proposal={
            "kind": ADD_ROOM_KIND,
            "payload": {
                "room_id": "hidden_forge",
                "name": "숨겨진 대장간",
                "description": "뜨거운 화덕이 남아 있다.",
                "exit_key": "forge",
                "exit_label": "대장간 쪽문",
                "exit_kind": "door",
                "return_exit_key": "back",
                "return_exit_label": "입구 홀",
                "loot": {"iron_ingot": 2},
                "clues": {"anvil_mark": "왕실 문장이 새겨져 있다."},
            },
        },
    )
    assert r3.status_code == 200, r3.text
    s3 = r3.json()["saga"]
    assert "hidden_forge" in s3["state"]["map"]["rooms"]
    assert any(e["key"] == "forge" for e in s3["scene_board"]["exits"])

    # 5. Deterministic replay of the entire branch succeeds identically.
    world_id = s3["world_id"]
    branch_id = s3["branch_id"]
    assert repo.replay(world_id, branch_id=branch_id) == repo.get_state(
        world_id, branch_id=branch_id
    )


# --- 2. OpenAICompatConfig: reasoning_effort, custom_headers, custom_body ------------------


def test_openai_compat_config_validation_and_body_merge():
    cfg = OpenAICompatConfig.from_values(
        base_url="https://openrouter.ai/api/v1",
        narrator_model="openai/gpt-5",
        api_key="sk-secret-key-123",
        reasoning_effort="high",
        custom_headers={"HTTP-Referer": "https://saga.example", "X-Title": "SAGA"},
        custom_body={"temperature": 0.4, "provider": {"sort": "throughput"}},
    )
    # Secrets and custom headers must never appear in repr
    assert "sk-secret-key-123" not in repr(cfg)
    assert "HTTP-Referer" not in repr(cfg)
    headers = cfg.headers()
    assert headers["Authorization"] == "Bearer sk-secret-key-123"
    assert headers["HTTP-Referer"] == "https://saga.example"
    body = cfg.build_body(
        {"model": "openai/gpt-5", "messages": [], "stream": False, "temperature": 0}
    )
    assert body["reasoning_effort"] == "high"
    assert body["temperature"] == 0.4
    assert body["provider"] == {"sort": "throughput"}
    assert body["model"] == "openai/gpt-5"

    # Security validations: CRLF injection, forbidden headers, reserved body keys, bad effort
    with pytest.raises(ValueError, match="invalid value"):
        OpenAICompatConfig.from_values(
            base_url="https://api.example.com/v1",
            narrator_model="m",
            custom_headers={"X-Bad": "val\r\nInjected: 1"},
        )
    with pytest.raises(ValueError, match="forbidden custom header"):
        OpenAICompatConfig.from_values(
            base_url="https://api.example.com/v1",
            narrator_model="m",
            custom_headers={"Host": "evil.example"},
        )
    with pytest.raises(ValueError, match="reserved keys"):
        OpenAICompatConfig.from_values(
            base_url="https://api.example.com/v1",
            narrator_model="m",
            custom_body={"messages": [{"role": "system", "content": "hijack"}]},
        )
    with pytest.raises(ValueError, match="reasoning_effort"):
        OpenAICompatConfig.from_values(
            base_url="https://api.example.com/v1",
            narrator_model="m",
            reasoning_effort="ultra_turbo",
        )


# --- 3. Multi-format card parsing (.json / .charx / .png) + deterministic & LLM conversion ---


def test_converter_parses_json_charx_png_and_rejects_zip_traversal():
    # JSON
    parsed_json = extract_card_from_dict(SAMPLE_CARD)
    assert parsed_json.name == "엘레나 폰 크로이츠"
    # Internal saga:rulebook metadata entry must be excluded
    assert len(parsed_json.lorebook) == 2

    draft = card_to_scenario_draft(parsed_json)
    assert draft.mode == "deterministic"
    assert "player" in draft.scenario.actors
    # Primary character had explicit "HP: 28/28" -> provenance "card"
    elena = next(
        a for aid, a in draft.scenario.actors.items() if a.display_name == "엘레나 폰 크로이츠"
    )
    assert elena.hp == 28 and elena.max_hp == 28
    assert elena.provenance["hp"] == "card"
    # NPC lorebook entry extracted with aliases & inferred HP
    marco = next(
        a for aid, a in draft.scenario.actors.items() if a.display_name == "NPC: 상인 마르코"
    )
    assert "Marco" in marco.aliases
    assert marco.provenance["hp"] == "inferred"
    # Location lorebook entry became a connected room
    assert draft.scenario.map is not None
    assert len(draft.scenario.map.rooms) == 2
    assert any("지하 서고" in r.name for r in draft.scenario.map.rooms.values())

    # .charx ZIP round-trip
    charx_bytes = write_charx(build_card_v3(SAMPLE_CARD))
    parsed_charx = extract_card_from_bytes(charx_bytes, filename="elena.charx")
    assert parsed_charx.source_format == "charx"
    assert parsed_charx.name == "엘레나 폰 크로이츠"

    # .png tEXt and zTXt
    png_text = _make_png_with_card(SAMPLE_CARD, chunk_type=b"tEXt", keyword=b"chara")
    assert extract_card_from_bytes(png_text, filename="elena.png").name == "엘레나 폰 크로이츠"
    png_ztxt = _make_png_with_card(SAMPLE_CARD, chunk_type=b"zTXt", keyword=b"ccv3")
    assert extract_card_from_bytes(png_ztxt, filename="elena.png").name == "엘레나 폰 크로이츠"

    # Path traversal inside .charx must be rejected
    bad_buf = io.BytesIO()
    with zipfile.ZipFile(bad_buf, "w") as zf:
        zf.writestr("../escape.txt", "boom")
        zf.writestr("card.json", json.dumps(SAMPLE_CARD))
    with pytest.raises(ConversionError, match="unsafe path"):
        extract_card_from_bytes(bad_buf.getvalue(), filename="evil.charx")


@pytest.mark.asyncio
async def test_enrich_card_with_llm_forwards_reasoning_headers_and_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        llm_scenario = {
            "title": "안개 낀 수도원",
            "summary": "은발의 기사 엘레나와 함께 지하 서고의 비밀을 파헤친다.",
            "actors": {
                "player": {
                    "display_name": "여행자",
                    "hp": 20,
                    "max_hp": 20,
                    "provenance": {"hp": "inferred"},
                },
                "elena": {
                    "display_name": "엘레나 폰 크로이츠",
                    "aliases": ["엘레나"],
                    "hp": 28,
                    "max_hp": 28,
                    "provenance": {"display_name": "card", "hp": "card", "stats": "inferred"},
                },
            },
            "map": {
                "start_room": "abbey_gate",
                "rooms": {
                    "abbey_gate": {
                        "name": "수도원 정문",
                        "description": "안개가 자욱한 낡은 정문.",
                        "exits": {
                            "down": {
                                "to_room": "crypt_library",
                                "label": "지하 계단",
                                "kind": "door",
                                "locked": True,
                            }
                        },
                    },
                    "crypt_library": {
                        "name": "수도원 지하 서고",
                        "description": "먼지 쌓인 고서와 제단.",
                        "exits": {
                            "up": {"to_room": "abbey_gate", "label": "정문으로", "kind": "door"}
                        },
                        "clues": {"altar_note": "봉인 해제 주문이 적혀 있다."},
                    },
                },
                "placements": {"player": "abbey_gate", "elena": "abbey_gate"},
            },
            "quest_state": {
                "abbey_secret": {"stage": 1, "status": "active", "objective": "지하 서고 조사"}
            },
            "narration": "안개 낀 수도원에서 이야기가 시작된다.",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(llm_scenario, ensure_ascii=False)}}]
            },
        )

    cfg = OpenAICompatConfig.from_values(
        base_url="https://llm.example.test/v1",
        narrator_model="test-model",
        api_key="sk-test",
        reasoning_effort="medium",
        custom_headers={"X-Custom-Trace": "saga-a2"},
        custom_body={"top_p": 0.85},
    )
    parsed = extract_card_from_dict(SAMPLE_CARD)
    result = await enrich_card_with_llm(parsed, cfg, transport=httpx.MockTransport(handler))
    assert result.mode == "llm"
    assert captured["headers"]["x-custom-trace"] == "saga-a2"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert captured["body"]["reasoning_effort"] == "medium"
    assert captured["body"]["top_p"] == 0.85
    assert result.scenario.actors["elena"].hp == 28
    assert result.scenario.map is not None
    assert result.scenario.map.rooms["abbey_gate"].exits["down"].locked is True


# --- 4. CLI import-card + FastAPI /v1/saga/convert & /converter security headers -------------


def test_cli_import_card_and_api_convert_and_converter_webapp(tmp_path):
    card_path = tmp_path / "elena.charx"
    card_path.write_bytes(write_charx(build_card_v3(SAMPLE_CARD)))
    out_sc = tmp_path / "elena.scenario.json"
    out_mod = tmp_path / "elena.module.json"

    code = cli_main(
        ["import-card", str(card_path), "--out", str(out_sc), "--module-out", str(out_mod)]
    )
    assert code == 0
    assert out_sc.is_file() and out_mod.is_file()
    mod_data = json.loads(out_mod.read_text(encoding="utf-8"))
    assert mod_data["type"] == "risuModule"
    assert any(e["comment"].startswith("saga:scenario:") for e in mod_data["module"]["lorebook"])

    # FastAPI endpoints
    app = create_app()
    client = TestClient(app)

    # /v1/saga/convert with JSON card
    res_json = client.post("/v1/saga/convert", json={"card": SAMPLE_CARD})
    assert res_json.status_code == 200, res_json.text
    body = res_json.json()
    assert body["mode"] == "deterministic"
    assert body["scenario"]["title"] == "엘레나 폰 크로이츠"
    assert body["module"]["type"] == "risuModule"

    # /v1/saga/convert with base64 .charx
    res_charx = client.post(
        "/v1/saga/convert",
        json={
            "file_base64": base64.b64encode(card_path.read_bytes()).decode("ascii"),
            "filename": "elena.charx",
        },
    )
    assert res_charx.status_code == 200, res_charx.text

    # SSRF guard on client-provided LLM URL
    res_ssrf = client.post(
        "/v1/saga/convert",
        json={
            "card": SAMPLE_CARD,
            "use_llm": True,
            "llm": {"base_url": "http://169.254.169.254/latest", "model": "m"},
        },
    )
    assert res_ssrf.status_code == 422

    # /converter standalone HTML webapp served with strict per-request nonce CSP & security headers
    page = client.get("/converter")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    csp = page.headers["content-security-policy"]
    assert "nonce-" in csp and "unsafe-inline" not in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-store"
    assert "innerHTML" not in page.text
    assert "localStorage" not in page.text
