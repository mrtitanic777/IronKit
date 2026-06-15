"""Heavy Iron .HO (HEB) archive reader/writer.

The "GoodEngine" archive format (big-endian on Xbox 360). Faithful reimplementation of
HiHoFile.dll's reader (decompiled), plus a patch-based writer that preserves the format's
opaque regions and fixes up offsets on edit (in-slot replace, or grow + relocate).

Layout:
  [0,2048)          HEL  - opaque header blob (preserved verbatim)
  [2048,+96)        MAST - 24 x int32(BE); int[15] = num -> SECT at num*2048
  [num*2048, ...)   SECT - 8 x int32(BE): [1] = layerCount; then layerCount x LayerEntry(64B)
  per layer:        data block at offToStart*0x800: AssetList + AssetData + Padding
  AssetEntry(32B):  totalDataSize, relOff, actualSize, unk0C, assetID(u64), assetType, unk1C
All multi-byte ints are big-endian.
"""
import os
import struct

# Known asset-type IDs (by total bytes they usually dominate)
ASSET_TYPES = {
    0xBDE21A8D: "RawBlob",          # pixel/geometry data blobs
    693241887: "TextureDescriptor",
    0x86EF2978: "StaticGeometry",
}


class R:
    __slots__ = ("d", "p")

    def __init__(self, d):
        self.d = d
        self.p = 0

    def i32(self):
        v = struct.unpack_from(">i", self.d, self.p)[0]; self.p += 4; return v

    def u32(self):
        v = struct.unpack_from(">I", self.d, self.p)[0]; self.p += 4; return v

    def u64(self):
        v = struct.unpack_from(">Q", self.d, self.p)[0]; self.p += 8; return v

    def raw(self, n):
        v = self.d[self.p:self.p + n]; self.p += n; return v


def parse(data):
    """Parse a .HO archive into a dict tree (header, layers, assets).

    Raises ValueError if the bytes don't look like a valid HEB/.HO archive (so non-archive or
    truncated input fails with a clear message rather than an opaque struct/index error).
    """
    if len(data) < 2048 + 96:
        raise ValueError("too small to be a .HO archive (%d bytes)" % len(data))
    r = R(data)
    hel = r.raw(2048)
    mast_off = r.p
    mast_ints = [r.i32() for _ in range(24)]
    num = mast_ints[15]
    sect_off = num * 2048
    if num <= 0 or sect_off + 32 > len(data):
        raise ValueError("not a valid .HO archive (bad MAST/SECT pointer)")
    r.p = sect_off
    sect_head = [r.i32() for _ in range(8)]
    nlayers = sect_head[1]
    if not 0 <= nlayers < 100000:
        raise ValueError("not a valid .HO archive (implausible layer count %d)" % nlayers)
    layers = [_layer(r, sect_off) for _ in range(nlayers)]
    for li, L in enumerate(layers):
        _get_assets(r, L, L["offToStart"] * 2048)
        if L["sub"]["kind"] == "PSL":
            for a in L["sub"]["assets"]:
                a["layer_idx"] = li             # tag each asset with its layer (for dup-id handling)
    return {"hel": hel, "mast_off": mast_off, "mast_ints": mast_ints,
            "sect_off": sect_off, "sect_head": sect_head, "layers": layers, "size": len(data)}


def _layer(r, sect_off):
    entry_pos = r.p
    lt = r.raw(4)
    r.i32()
    [r.i32() for _ in range(5)]
    offToStart = r.i32()
    totalLayerSize = r.i32()
    totalLayerSize2 = r.i32()
    [r.i32() for _ in range(4)]
    offToPart2 = r.i32()
    r.i32()
    save = r.p
    r.p = sect_off + offToPart2
    tag = r.raw(4)
    if tag == b"PSL\x00":
        sub = _psl(r)
    elif tag == b"PSLD":
        sub = {"kind": "PSLD", "sectionSize": r.i32(), "vals": [r.i32() for _ in range(6)]}
    else:
        raise ValueError("bad sublayer tag %r at 0x%X" % (tag, r.p - 4))
    r.p = save
    return {"entry_pos": entry_pos, "type": lt, "offToStart": offToStart,
            "totalLayerSize": totalLayerSize, "totalLayerSize2": totalLayerSize2,
            "offToPart2": offToPart2, "sub": sub}


def _psl(r):
    sectionSize, amt, unknown = r.i32(), r.i32(), r.i32()
    subs = []
    for _ in range(amt):
        pos = r.p
        subs.append({"pos": pos, "type": r.i32(), "startOffset": r.i32(),
                     "size": r.i32(), "minusOne": r.i32()})
    return {"kind": "PSL", "sectionSize": sectionSize, "amt": amt,
            "unknown": unknown, "subs": subs, "assets": []}


def _get_assets(r, L, layerStart):
    sub = L["sub"]
    if sub["kind"] != "PSL":
        return
    for ss in sub["subs"]:
        if ss["type"] == 0:                       # AssetList
            r.p = layerStart + ss["startOffset"]
            count = r.i32()
            r.p += 28
            for _ in range(count):
                hdr_pos = r.p
                totalDataSize = r.i32()
                relOff = r.i32()
                absSizeOff = r.p
                actualSize = r.i32()
                r.i32()
                assetID = r.u64()
                assetType = r.u32()
                r.i32()
                sub["assets"].append({
                    "hdr_pos": hdr_pos, "totalDataSize": totalDataSize, "relOff": relOff,
                    "absSizeOff": absSizeOff, "actualSize": actualSize, "assetID": assetID,
                    "assetType": assetType, "absDataOff": layerStart + relOff,
                    "layerStart": layerStart})


def all_assets(P):
    out = []
    for L in P["layers"]:
        if L["sub"]["kind"] == "PSL":
            out.extend(L["sub"]["assets"])
    return out


def find_assets(P, assetID, layer_idx=None):
    """Every asset matching assetID, optionally restricted to one layer."""
    return [a for a in all_assets(P)
            if a["assetID"] == assetID and (layer_idx is None or a.get("layer_idx") == layer_idx)]


def find_asset(P, assetID, layer_idx=None):
    """The single asset matching assetID; None if absent.

    Raises ValueError if the id appears in multiple layers and no layer_idx is given -- the
    shipping game (e.g. MNUS.ho) has hundreds of duplicate ids, so silently taking the first
    would be a correctness bug. Pass a layer to disambiguate.
    """
    ms = find_assets(P, assetID, layer_idx)
    if not ms:
        return None
    if len(ms) > 1:
        raise ValueError("asset %016X is ambiguous: %d instances in layers %s (pass a layer)"
                         % (assetID, len(ms), sorted(set(a.get("layer_idx") for a in ms))))
    return ms[0]


def asset_bytes(data, a):
    return data[a["absDataOff"]:a["absDataOff"] + a["actualSize"]]


def align(n, a):
    return (n + a - 1) // a * a


def repack_inslot(data, edits):
    """edits: list of (asset, newbytes), each <= the asset's slot. In-place patch, no cascade.
    The slot tail is zero-filled, so this is not byte-identical to the source when the original
    padding was non-zero (harmless to the engine)."""
    buf = bytearray(data)
    for a, nd in edits:
        if len(nd) > a["totalDataSize"]:
            raise ValueError("asset %016X: %d bytes > slot %d (use replace())"
                             % (a["assetID"], len(nd), a["totalDataSize"]))
        off = a["absDataOff"]
        buf[off:off + len(nd)] = nd
        for i in range(off + len(nd), off + a["actualSize"]):
            buf[i] = 0
        struct.pack_into(">i", buf, a["absSizeOff"], len(nd))
    return buf


def replace(P, data, assetID, newdata, layer_idx=None):
    """Replace an asset with newdata of ANY size. Same slot -> in-place; else rebuild that
    layer's 0x800-aligned block, relocate to EOF, and fix offToStart + sizes. Pass layer_idx
    to target one instance of a duplicated id (see find_asset)."""
    if layer_idx is None and len(find_assets(P, assetID)) > 1:
        raise ValueError("asset %016X is ambiguous (multiple layers); pass a layer index" % assetID)
    buf = bytearray(data)
    found = None
    for li, L in enumerate(P["layers"]):
        if layer_idx is not None and li != layer_idx:
            continue
        if L["sub"]["kind"] != "PSL":
            continue
        for a in L["sub"]["assets"]:
            if a["assetID"] == assetID:
                found = (L, a)
                break
        if found:
            break
    if not found:
        raise KeyError("asset %016X not found" % assetID)
    L, a = found
    oldSlot, newActual = a["totalDataSize"], len(newdata)
    newSlot = align(newActual, 64)
    if newSlot == oldSlot:
        off = a["absDataOff"]
        buf[off:off + newActual] = newdata
        for i in range(off + newActual, off + oldSlot):
            buf[i] = 0
        struct.pack_into(">i", buf, a["absSizeOff"], newActual)
        return buf

    layerStart = L["offToStart"] * 2048
    assets = sorted(L["sub"]["assets"], key=lambda x: x["relOff"])
    adStart = assets[0]["relOff"]
    block = bytearray(data[layerStart:layerStart + adStart])
    adata = bytearray()
    newrel = {}
    for aj in assets:
        newrel[id(aj)] = adStart + len(adata)
        if aj is a:
            adata += bytes(newdata) + b"\x00" * (newSlot - newActual)
        else:
            adata += data[aj["absDataOff"]:aj["absDataOff"] + aj["totalDataSize"]]
    block += adata
    newTotal = align(adStart + len(adata), 0x800)
    block += b"\x00" * (newTotal - len(block))
    for aj in assets:
        hr = aj["hdr_pos"] - layerStart
        struct.pack_into(">i", block, hr + 4, newrel[id(aj)])
        if aj is a:
            struct.pack_into(">i", block, hr + 0, newSlot)
            struct.pack_into(">i", block, hr + 8, newActual)
    while len(buf) % 0x800 != 0:
        buf.append(0)
    newBlockOff = len(buf)
    buf += block
    ep = L["entry_pos"]
    struct.pack_into(">i", buf, ep + 28, newBlockOff // 2048)
    struct.pack_into(">i", buf, ep + 32, newTotal)
    struct.pack_into(">i", buf, ep + 36, newTotal)
    for ss in L["sub"]["subs"]:
        if ss["type"] == 1:
            struct.pack_into(">i", buf, ss["pos"] + 8, len(adata))
    return buf


# ----------------------------------------------------------------------------- CLI
def _load(path):
    data = open(path, "rb").read()
    return data, parse(data)


def _info(args):
    data, P = _load(args.file)
    A = all_assets(P)
    print("file   : %s" % args.file)
    print("size   : 0x%X (%d bytes)" % (P["size"], P["size"]))
    print("layers : %d" % len(P["layers"]))
    print("assets : %d" % len(A))
    from collections import Counter
    cnt, siz = Counter(), Counter()
    for a in A:
        cnt[a["assetType"]] += 1
        siz[a["assetType"]] += a["actualSize"]
    print("asset types (by total bytes):")
    for ty, _ in siz.most_common():
        print("  0x%08X %-18s count=%-5d bytes=%d"
              % (ty, ASSET_TYPES.get(ty, ""), cnt[ty], siz[ty]))


def _list(args):
    data, P = _load(args.file)
    A = all_assets(P)
    if args.type is not None:
        A = [a for a in A if a["assetType"] == args.type]
    for a in A:
        print("%016X  type=0x%08X %-18s actual=%-9d slot=%-9d off=0x%X"
              % (a["assetID"], a["assetType"], ASSET_TYPES.get(a["assetType"], ""),
                 a["actualSize"], a["totalDataSize"], a["absDataOff"]))


def _layers(args):
    data, P = _load(args.file)
    for i, L in enumerate(P["layers"]):
        sub = L["sub"]
        na = len(sub["assets"]) if sub["kind"] == "PSL" else 0
        print("[%2d] %-6r block@0x%-7X size=%-9d %-4s assets=%d"
              % (i, L["type"], L["offToStart"] * 2048, L["totalLayerSize"], sub["kind"], na))


def _extract(args):
    data, P = _load(args.file)
    A = all_assets(P)
    if args.type is not None:
        A = [a for a in A if a["assetType"] == args.type]
    os.makedirs(args.out, exist_ok=True)
    n = 0
    for a in A:
        fn = os.path.join(args.out, "%016X_%08X.bin" % (a["assetID"], a["assetType"]))
        with open(fn, "wb") as f:
            f.write(asset_bytes(data, a))
        n += 1
    print("extracted %d assets -> %s" % (n, args.out))


def _replace(args):
    data, P = _load(args.file)
    newdata = open(args.data, "rb").read()
    out = replace(P, data, args.id, newdata, layer_idx=args.layer)
    with open(args.out, "wb") as f:
        f.write(out)
    print("replaced %016X (%d bytes) ; %d -> %d, saved %s"
          % (args.id, len(newdata), len(data), len(out), args.out))


def _dups(args):
    data, P = _load(args.file)
    from collections import defaultdict
    m = defaultdict(set)
    for a in all_assets(P):
        m[a["assetID"]].add(a.get("layer_idx"))
    dups = {k: v for k, v in m.items() if len(v) > 1}
    print("%d asset id(s) appear in multiple layers (use --layer to disambiguate):" % len(dups))
    for k in sorted(dups):
        print("  %016X  layers=%s" % (k, sorted(dups[k])))


def add_parser(sub):
    p = sub.add_parser("ho", help="Heavy Iron HEB/.HO archives")
    s = p.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("info", help="summary + asset-type breakdown"); a.add_argument("file"); a.set_defaults(func=_info)
    a = s.add_parser("list", help="list assets"); a.add_argument("file")
    a.add_argument("--type", type=lambda x: int(x, 0)); a.set_defaults(func=_list)
    a = s.add_parser("layers", help="list layers"); a.add_argument("file"); a.set_defaults(func=_layers)
    a = s.add_parser("extract", help="dump raw asset bytes to a folder"); a.add_argument("file")
    a.add_argument("-o", "--out", required=True); a.add_argument("--type", type=lambda x: int(x, 0))
    a.set_defaults(func=_extract)
    a = s.add_parser("dups", help="list asset ids that appear in more than one layer")
    a.add_argument("file"); a.set_defaults(func=_dups)
    a = s.add_parser("replace", help="replace one asset's bytes (any size)"); a.add_argument("file")
    a.add_argument("--id", required=True, type=lambda x: int(x, 16)); a.add_argument("--data", required=True)
    a.add_argument("--layer", type=int, default=None, help="layer index (for duplicated ids)")
    a.add_argument("-o", "--out", required=True); a.set_defaults(func=_replace)
