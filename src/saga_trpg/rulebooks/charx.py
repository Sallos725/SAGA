"""Character cards as `.charx`: a zip with `card.json` (CCv3), an icon and an optional module.

Read from the host's `importCharacterProcess`/`CharXImporter` (docs/RISUAI_MODULES.md):
`card.json` must have `spec: "chara_card_v3"`; `module.risum`, when present, supplies the
card's lorebook, triggers and regex scripts; every other file is an asset addressed by
`embeded://<path>` from `data.assets`. A card bundled with a rulebook module therefore carries
the rulebook as the character's own lorebook and needs no separate module import.
"""

from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib
from typing import Any

from saga_trpg.rulebooks.risum import RPackError, RPackMap, write_risum

ICON_PATH = "assets/icon/image/main.png"
_CCV3_FIELDS = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "creator_notes",
    "system_prompt",
    "post_history_instructions",
    "alternate_greetings",
    "tags",
    "creator",
    "character_version",
    "extensions",
)


def build_card_v3(card: dict[str, Any]) -> dict[str, Any]:
    """Lift a chara_card_v2 (or v3) JSON into the CCv3 shape the host imports from charx."""
    data = card.get("data", card)
    lifted: dict[str, Any] = {}
    for field in _CCV3_FIELDS:
        value = data.get(field)
        if field in ("alternate_greetings", "tags"):
            lifted[field] = list(value or [])
        elif field == "extensions":
            lifted[field] = dict(value or {})
        else:
            lifted[field] = value or ""
    lifted["group_only_greetings"] = list(data.get("group_only_greetings") or [])
    # Optional CCv3 fields and the card's own lorebook travel unchanged when present.
    for field in (
        "character_book",
        "nickname",
        "creator_notes_multilingual",
        "source",
        "creation_date",
        "modification_date",
    ):
        if data.get(field) is not None:
            lifted[field] = data[field]
    lifted["assets"] = [
        {"type": "icon", "uri": f"embeded://{ICON_PATH}", "name": "main", "ext": "png"}
    ]
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": lifted}


def placeholder_icon(size: int = 64, rgb: tuple[int, int, int] = (58, 64, 90)) -> bytes:
    """A small solid PNG so the host has a main icon; pure Python, no image library."""
    row = b"\x00" + bytes(rgb) * size
    raw = row * size

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def write_charx(
    card: dict[str, Any],
    *,
    module: dict[str, Any] | None = None,
    rpack: RPackMap | None = None,
    icon: bytes | None = None,
) -> bytes:
    if module is not None and rpack is None:
        raise RPackError("a module inside a .charx needs the host's rpack table")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("card.json", json.dumps(card, ensure_ascii=False, indent=4))
        if module is not None:
            archive.writestr("module.risum", write_risum(module, rpack))
        archive.writestr(ICON_PATH, icon or placeholder_icon())
    return buffer.getvalue()
