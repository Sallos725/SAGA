import base64
import io
import json
import random
import struct
import zipfile

import pytest

from saga_trpg.rulebooks.charx import build_card_v3, write_charx
from saga_trpg.rulebooks.loader import rulebook_path
from saga_trpg.rulebooks.risu_module import build_module, find_rulebook_entries
from saga_trpg.rulebooks.risum import (
    RPackError,
    RPackMap,
    extract_rpack_map,
    read_risum,
    write_risum,
)

SRD_TEXT = rulebook_path("srd-5.2.1").read_text(encoding="utf-8")
CARD_PATH = "packs/risu-starter/saga-srd-demo.card.json"


def fake_map(seed=7):
    encode = list(range(256))
    random.Random(seed).shuffle(encode)
    decode = [0] * 256
    for plain, coded in enumerate(encode):
        decode[coded] = plain
    return bytes(encode) + bytes(decode)


# --- RPack map ------------------------------------------------------------------------------


def test_rpack_map_must_be_512_bytes_and_a_true_inverse_pair():
    table = RPackMap(fake_map())
    assert table.decode(table.encode(b"saga")) == b"saga"
    with pytest.raises(RPackError, match="512"):
        RPackMap(b"\x00" * 100)
    broken = bytearray(fake_map())
    broken[300] ^= 1
    with pytest.raises(RPackError, match="inverse"):
        RPackMap(bytes(broken))


def test_rpack_map_is_extracted_from_the_host_bundle_not_bundled_here(tmp_path):
    table = fake_map()
    data_url = "data:application/octet-stream;base64," + base64.b64encode(table).decode()
    (tmp_path / "other.js").write_text("console.log(1)")
    (tmp_path / "database.js").write_text(
        f"var Xb=`{data_url}`;async function ex(){{let e=await(await fetch(Xb)).arrayBuffer();"
        "Zb=new Uint8Array(e),Qb=Zb.slice(0,256),$b=Zb.slice(256,512)}"
    )
    assert extract_rpack_map(tmp_path) == table
    assert extract_rpack_map(tmp_path / "database.js") == table
    with pytest.raises(RPackError, match="no RPack"):
        extract_rpack_map(tmp_path / "other.js")


# --- .risum --------------------------------------------------------------------------------


def test_risum_layout_matches_the_host_reader_and_round_trips():
    table = RPackMap(fake_map())
    module = build_module(SRD_TEXT)
    data = write_risum(module, table)
    assert data[0] == 111 and data[1] == 0
    (length,) = struct.unpack_from("<I", data, 2)
    main = table.decode(data[6 : 6 + length])
    envelope = json.loads(main)
    assert envelope["type"] == "risuModule"
    assert envelope["module"]["assets"] == []
    assert data[6 + length] == 0 and len(data) == 7 + length
    restored = read_risum(data, table)
    assert restored == {**module, "assets": []}
    found = find_rulebook_entries(restored["lorebook"])
    assert found and found[0].content == SRD_TEXT


def test_risum_reader_rejects_wrong_magic_or_type():
    table = RPackMap(fake_map())
    with pytest.raises(RPackError, match="magic"):
        read_risum(b"\x00\x00", table)
    bogus = table.encode(json.dumps({"type": "other", "module": {}}).encode())
    with pytest.raises(RPackError, match="risuModule"):
        read_risum(bytes([111, 0]) + struct.pack("<I", len(bogus)) + bogus + b"\x00", table)


# --- .charx --------------------------------------------------------------------------------


def test_card_v3_keeps_the_starter_card_text_and_declares_an_embedded_icon():
    card = build_card_v3(json.load(open(CARD_PATH, encoding="utf-8")))
    assert card["spec"] == "chara_card_v3" and card["spec_version"] == "3.0"
    data = card["data"]
    assert data["name"] and data["first_mes"].startswith("SAGA")
    assert data["group_only_greetings"] == []
    assert data["assets"] == [
        {
            "type": "icon",
            "uri": "embeded://assets/icon/image/main.png",
            "name": "main",
            "ext": "png",
        }
    ]
    assert "character_book" not in data


def test_charx_is_a_zip_with_card_json_icon_and_optional_module(tmp_path):
    table = RPackMap(fake_map())
    card = build_card_v3(json.load(open(CARD_PATH, encoding="utf-8")))
    module = build_module(SRD_TEXT)
    payload = write_charx(card, module=module, rpack=table)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert names == {"card.json", "module.risum", "assets/icon/image/main.png"}
        assert json.loads(archive.read("card.json"))["spec"] == "chara_card_v3"
        assert archive.read("assets/icon/image/main.png").startswith(b"\x89PNG")
        restored = read_risum(archive.read("module.risum"), table)
        assert find_rulebook_entries(restored["lorebook"])[0].content == SRD_TEXT
    bare = write_charx(card)
    with zipfile.ZipFile(io.BytesIO(bare)) as archive:
        assert set(archive.namelist()) == {"card.json", "assets/icon/image/main.png"}
    with pytest.raises(RPackError, match="rpack"):
        write_charx(card, module=module)


def test_cli_writes_risum_and_charx_files(tmp_path, capsys):
    from saga_trpg.rulebooks.__main__ import main

    table = tmp_path / "rpack_map.bin"
    table.write_bytes(fake_map())
    risum = tmp_path / "srd.risum"
    assert (
        main(
            [
                "risum",
                str(rulebook_path("srd-5.2.1")),
                "--rpack-map",
                str(table),
                "--out",
                str(risum),
            ]
        )
        == 0
    )
    assert risum.read_bytes()[0] == 111
    charx = tmp_path / "srd.charx"
    assert (
        main(
            [
                "charx",
                CARD_PATH,
                "--rulebook",
                str(rulebook_path("srd-5.2.1")),
                "--rpack-map",
                str(table),
                "--out",
                str(charx),
            ]
        )
        == 0
    )
    assert zipfile.is_zipfile(charx)
    assert main(["risum", str(rulebook_path("srd-5.2.1")), "--out", str(risum)]) == 1
    assert "rpack" in capsys.readouterr().err.lower()


def test_card_v3_preserves_optional_v3_fields_and_the_character_book():
    card = json.load(open(CARD_PATH, encoding="utf-8"))
    card["data"]["character_book"] = {"entries": [{"keys": ["key"], "content": "lore"}]}
    card["data"]["nickname"] = "Saga"
    card["data"]["source"] = ["https://example.invalid"]
    card["data"]["creation_date"] = 1
    lifted = build_card_v3(card)["data"]
    assert lifted["character_book"] == card["data"]["character_book"]
    assert lifted["nickname"] == "Saga" and lifted["source"] == ["https://example.invalid"]
    assert lifted["creation_date"] == 1 and "modification_date" not in lifted


def test_risum_reader_validates_the_whole_frame():
    table = RPackMap(fake_map())
    good = write_risum(build_module(SRD_TEXT), table)
    with pytest.raises(RPackError, match="terminator"):
        read_risum(good[:-1], table)
    with pytest.raises(RPackError, match="trailing"):
        read_risum(good + b"\x00", table)
    with pytest.raises(RPackError, match="truncated"):
        read_risum(good[:-1] + b"\x01\xff\xff\xff\xff", table)
    asset = good[:-1] + b"\x01" + struct.pack("<I", 3) + b"abc" + b"\x00"
    assert read_risum(asset, table)["name"].startswith("SAGA")


def test_rpack_map_cli_reports_a_missing_source_without_a_traceback(tmp_path, capsys):
    from saga_trpg.rulebooks.__main__ import main

    assert main(["rpack-map", str(tmp_path / "missing.js"), "--out", str(tmp_path / "m.bin")]) == 1
    assert "missing.js" in capsys.readouterr().err
