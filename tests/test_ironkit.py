"""IronKit tests.

Pure unit tests run anywhere. The integration tests need real game files and are skipped
unless you point env vars at them:
    set IRONKIT_TEST_HO=...\\MNUS.ho
    set IRONKIT_TEST_FONT=...\\FONT.ho
Run with:  py -m pytest   (from the repo root, so `import ironkit` resolves)
"""
import os

import pytest

from ironkit import archive, texture, font
from ironkit.bits import Bits

RAWBLOB = 0xBDE21A8D


# --------------------------------------------------------------- pure unit tests
def test_bits_unsigned_and_signed():
    b = Bits(bytes([0b10110000]))
    assert b.read(1) == 1
    assert b.read(3) == 0b011
    assert Bits(bytes([0b11110000])).reads(4) == -1          # 1111 -> -1 signed


def test_align():
    assert archive.align(0, 64) == 0
    assert archive.align(1, 64) == 64
    assert archive.align(64, 64) == 64
    assert archive.align(65, 0x800) == 0x800


def test_swap16_is_involution():
    src = bytes(range(16))
    sw = texture.swap16(src)
    assert sw[0] == 1 and sw[1] == 0                         # adjacent pair swapped
    assert texture.swap16(sw) == src                         # swapping twice restores


def test_tiled_offset_injective():
    seen = {texture.tiled_offset(x, y, 32, 4) for y in range(8) for x in range(8)}
    assert texture.tiled_offset(0, 0, 32, 4) == 0
    assert len(seen) == 64                                   # distinct blocks -> distinct offsets


def test_parse_rejects_non_archive():
    with pytest.raises(ValueError):
        archive.parse(b"definitely not a .HO file " * 200)


# --------------------------------------------------------------- integration (gated)
HO = os.environ.get("IRONKIT_TEST_HO")
FONT = os.environ.get("IRONKIT_TEST_FONT")
_need_ho = pytest.mark.skipif(not HO or not os.path.exists(HO or ""),
                              reason="set IRONKIT_TEST_HO to a .HO file")
_need_font = pytest.mark.skipif(not FONT or not os.path.exists(FONT or ""),
                                reason="set IRONKIT_TEST_FONT to FONT.ho")


def _pick(P, min_size, exact_slot=True):
    for x in archive.all_assets(P):
        if x["assetType"] == RAWBLOB and x["actualSize"] >= min_size and \
                (not exact_slot or archive.align(x["actualSize"], 64) == x["totalDataSize"]):
            return x
    raise AssertionError("no suitable asset")


@_need_ho
def test_archive_inslot_roundtrip():
    data = open(HO, "rb").read()
    P = archive.parse(data)
    a = _pick(P, 64)
    out = bytes(archive.replace(P, data, a["assetID"], archive.asset_bytes(data, a),
                                layer_idx=a["layer_idx"]))
    assert len(out) == len(data)                             # same slot -> no growth
    a2 = archive.find_assets(archive.parse(out), a["assetID"], a["layer_idx"])[0]
    assert a2["actualSize"] == a["actualSize"]


@_need_ho
def test_archive_grow_relocate():
    data = open(HO, "rb").read()
    P = archive.parse(data)
    a = _pick(P, 4096, exact_slot=False)
    nd = b"\xAB" * (a["actualSize"] + 50000)
    out = bytes(archive.replace(P, data, a["assetID"], nd, layer_idx=a["layer_idx"]))
    assert len(out) > len(data)
    got = archive.find_assets(archive.parse(out), a["assetID"], a["layer_idx"])[0]
    assert got["actualSize"] == len(nd)
    assert out[got["absDataOff"]:got["absDataOff"] + len(nd)] == nd


@_need_ho
def test_duplicate_id_raises_without_layer():
    data = open(HO, "rb").read()
    P = archive.parse(data)
    dup = next((k for k in (a["assetID"] for a in archive.all_assets(P))
                if len(archive.find_assets(P, k)) > 1), None)
    if dup is None:
        pytest.skip("no duplicate ids in this archive")
    with pytest.raises(ValueError):
        archive.find_asset(P, dup)                           # ambiguous -> must raise


@_need_font
def test_font_extract(tmp_path):
    from fontTools.ttLib import TTFont
    out = str(tmp_path / "f.ttf")
    name, n = font.extract(open(FONT, "rb").read(), 2, out)
    assert n > 0
    tt = TTFont(out)
    assert "glyf" in tt and "cmap" in tt
    assert ord("A") in tt.getBestCmap()
