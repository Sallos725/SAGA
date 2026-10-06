"""The host's legacy binary module file (`.risum`) and its RPack byte table.

Layout, read from the host's `exportModuleLegacy`/`readModule` (docs/RISUAI_MODULES.md):
byte 111, byte 0 (version), u32 LE length, RPack(JSON `{type: "risuModule", module}`), then per
asset byte 1 + u32 LE length + RPack(bytes), then byte 0. RPack is a fixed 256-byte
substitution table (encode) followed by its inverse (decode): 512 bytes in all.

The table is part of the host, not of SAGA, and is not bundled here. `extract_rpack_map` reads
it from the host's own shipped bundle on the user's machine.
"""

from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path
from typing import Any

_MAGIC = 111
_VERSION = 0
_DATA_URL = re.compile(rb"data:application/octet-stream;base64,([A-Za-z0-9+/=]{680,700})")


class RPackError(ValueError):
    """The RPack table is missing or malformed, or a `.risum` file is not one."""


class RPackMap:
    def __init__(self, table: bytes) -> None:
        if len(table) != 512:
            raise RPackError(f"RPack table must be 512 bytes, got {len(table)}")
        encode, decode = table[:256], table[256:]
        if sorted(encode) != list(range(256)) or any(decode[encode[i]] != i for i in range(256)):
            raise RPackError("RPack table halves are not an inverse pair")
        self.table = bytes(table)
        self._encode = bytes.maketrans(bytes(range(256)), encode)
        self._decode = bytes.maketrans(bytes(range(256)), decode)

    @classmethod
    def load(cls, path: Path | str) -> RPackMap:
        return cls(Path(path).read_bytes())

    def encode(self, data: bytes) -> bytes:
        return data.translate(self._encode)

    def decode(self, data: bytes) -> bytes:
        return data.translate(self._decode)


def extract_rpack_map(source: Path | str) -> bytes:
    """Find the host's inlined 512-byte table in a shipped bundle file or directory."""
    source = Path(source)
    files = sorted(source.glob("*.js")) if source.is_dir() else [source]
    for file in files:
        for match in _DATA_URL.finditer(file.read_bytes()):
            try:
                table = base64.b64decode(match[1], validate=True)
                RPackMap(table)
            except (ValueError, RPackError):
                continue
            return table
    raise RPackError(f"no RPack table found in {source}; point at the host's dist/assets")


def write_risum(module: dict[str, Any], rpack: RPackMap) -> bytes:
    """A `.risum` with no assets (rulebook modules carry text only)."""
    document = dict(module)
    document["assets"] = []
    envelope = {"module": document, "type": "risuModule"}
    main = rpack.encode(json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8"))
    return bytes([_MAGIC, _VERSION]) + struct.pack("<I", len(main)) + main + b"\x00"


def read_risum(data: bytes, rpack: RPackMap) -> dict[str, Any]:
    """Parse a `.risum` the way the host does, ignoring assets."""
    if len(data) < 7 or data[0] != _MAGIC:
        raise RPackError("not a .risum file (bad magic)")
    if data[1] != _VERSION:
        raise RPackError(f"unsupported .risum version {data[1]}")
    (length,) = struct.unpack_from("<I", data, 2)
    if 6 + length > len(data):
        raise RPackError("truncated .risum: module payload is shorter than its length")
    main = rpack.decode(data[6 : 6 + length])
    try:
        envelope = json.loads(main)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RPackError("module payload did not decode; wrong RPack table?") from None
    if envelope.get("type") != "risuModule" or not isinstance(envelope.get("module"), dict):
        raise RPackError("payload is not a risuModule envelope")
    # Walk the asset records to the terminator so a malformed frame is never reported valid.
    position = 6 + length
    while True:
        if position >= len(data):
            raise RPackError("truncated .risum: missing end-of-file terminator")
        marker = data[position]
        position += 1
        if marker == 0:
            break
        if marker != 1 or position + 4 > len(data):
            raise RPackError("truncated .risum: malformed asset record")
        (asset_length,) = struct.unpack_from("<I", data, position)
        position += 4 + asset_length
        if position > len(data):
            raise RPackError("truncated .risum: asset is shorter than its length")
    if position != len(data):
        raise RPackError("malformed .risum: trailing bytes after the terminator")
    return envelope["module"]
