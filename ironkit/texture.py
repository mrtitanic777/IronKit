"""Xbox 360 texture decoding (tiled DXT1/DXT5) -> RGBA.

A texture is a 72-byte descriptor asset pointing at a separate pixel blob (blob id =
descriptor id | bit44). Descriptor fields: format byte @0x33 (0x52=DXT1, 0x54=DXT5),
packed dims @0x34 (w-1 low 13 bits, h-1 next 13). Pixels are 360-tiled and 8-in-16
byte-swapped, so we un-tile (Xenia GetTiledOffset2D), byte-swap each block, then DXT decode.
"""
import os
import struct

from . import archive

TEXTURE_TYPE = 693241887


def parse_desc(desc):
    blob_id = struct.unpack_from(">Q", desc, 0)[0]
    fmt = desc[0x33]
    sz = struct.unpack_from(">I", desc, 0x34)[0]
    return blob_id, fmt, (sz & 0x1FFF) + 1, ((sz >> 13) & 0x1FFF) + 1


def _rgb565(c):
    return (((c >> 11) & 0x1F) * 255 // 31, ((c >> 5) & 0x3F) * 255 // 63, (c & 0x1F) * 255 // 31)


def _color_block(cb, dxt1):
    c0 = cb[0] | (cb[1] << 8)
    c1 = cb[2] | (cb[3] << 8)
    idx = cb[4] | (cb[5] << 8) | (cb[6] << 16) | (cb[7] << 24)
    col = [_rgb565(c0), _rgb565(c1)]
    if (not dxt1) or c0 > c1:
        col.append(tuple((2 * col[0][i] + col[1][i]) // 3 for i in range(3)))
        col.append(tuple((col[0][i] + 2 * col[1][i]) // 3 for i in range(3)))
    else:
        col.append(tuple((col[0][i] + col[1][i]) // 2 for i in range(3)))
        col.append((0, 0, 0))
    return [col[(idx >> (2 * i)) & 3] for i in range(16)]


def _alpha_block(ab):
    a0, a1 = ab[0], ab[1]
    bits = int.from_bytes(ab[2:8], "little")
    pal = [a0, a1]
    if a0 > a1:
        pal += [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
    else:
        pal += [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)] + [0, 255]
    return [pal[(bits >> (3 * i)) & 7] for i in range(16)]


def tiled_offset(x, y, width_blocks, bpp_log2):
    """Xenia GetTiledOffset2D - byte offset of block (x,y) in 360-tiled data."""
    aligned = (width_blocks + 31) & ~31
    macro = ((y >> 5) * (aligned >> 5) + (x >> 5)) << (bpp_log2 + 7)
    micro = (((y & 6) << 2) + (x & 7)) << bpp_log2
    offset = macro + ((micro & ~0xF) << 1) + (micro & 0xF) + ((y & 8) << (3 + bpp_log2)) + ((y & 1) << 4)
    return (((offset & ~0x1FF) << 3) + ((y & 16) << 7) + ((offset & 0x1C0) << 2) +
            ((((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) + (offset & 0x3F))


def swap16(b):
    """Xbox 360 8-in-16 endian swap: swap every adjacent byte pair."""
    a = bytearray(len(b) & ~1)
    a[0::2] = b[1:len(a):2]
    a[1::2] = b[0:len(a):2]
    return bytes(a)


def decode(blob, w, h, fmt, tiled=True, force_opaque=False):
    """Decode a tiled DXT1(0x52)/DXT5(0x54) blob to a Pillow RGBA Image."""
    import numpy as np
    from PIL import Image
    bpp = 8 if fmt == 0x52 else 16
    bpp_log2 = 3 if bpp == 8 else 4
    bw, bh = (w + 3) // 4, (h + 3) // 4
    img = np.zeros((bh * 4, bw * 4, 4), np.uint8)
    for by in range(bh):
        for bx in range(bw):
            o = tiled_offset(bx, by, bw, bpp_log2) if tiled else (by * bw + bx) * bpp
            blk = blob[o:o + bpp]
            if len(blk) < bpp:
                continue
            blk = swap16(blk)
            if bpp == 8:
                cols = _color_block(blk, True); al = [255] * 16
            else:
                al = [255] * 16 if force_opaque else _alpha_block(blk[0:8])
                cols = _color_block(blk[8:16], False)
            for i in range(16):
                r, g, b = cols[i]
                img[by * 4 + i // 4, bx * 4 + i % 4] = (r, g, b, al[i])
    return Image.fromarray(img[:h, :w], "RGBA")


def decode_descriptor(data, P, descID, **kw):
    """Decode a texture by descriptor asset-ID from a parsed .HO; returns (image, w, h, fmt)."""
    da = archive.find_asset(P, descID)
    desc = archive.asset_bytes(data, da)
    blob_id, fmt, w, h = parse_desc(desc)
    ba = archive.find_asset(P, blob_id)
    blob = archive.asset_bytes(data, ba)
    return decode(blob, w, h, fmt, **kw), w, h, fmt


# ----------------------------------------------------------------------------- CLI
def _decode(args):
    data = open(args.file, "rb").read()
    P = archive.parse(data)
    img, w, h, fmt = decode_descriptor(data, P, args.id, force_opaque=args.opaque)
    out = args.out or ("%016X_%dx%d.png" % (args.id, w, h))
    img.save(out)
    print("decoded %016X  %dx%d  fmt=0x%X -> %s" % (args.id, w, h, fmt, out))


def _batch(args):
    data = open(args.file, "rb").read()
    P = archive.parse(data)
    A = archive.all_assets(P)
    lut = {a["assetID"]: a for a in A}
    os.makedirs(args.out, exist_ok=True)
    ok = skip = 0
    done = set()
    for da in [a for a in A if a["assetType"] == TEXTURE_TYPE]:
        if da["assetID"] in done:
            continue
        done.add(da["assetID"])
        desc = archive.asset_bytes(data, da)
        if len(desc) < 0x38:
            skip += 1; continue
        blob_id, fmt, w, h = parse_desc(desc)
        ba = lut.get(blob_id)
        if fmt not in (0x52, 0x54) or not ba or w > 4096 or h > 4096:
            skip += 1; continue
        blob = archive.asset_bytes(data, ba)
        if len(blob) < ((w + 3) // 4) * ((h + 3) // 4) * (8 if fmt == 0x52 else 16):
            skip += 1; continue
        try:
            decode(blob, w, h, fmt).save(os.path.join(args.out, "%016X_%dx%d.png" % (da["assetID"], w, h)))
            ok += 1
        except Exception:
            skip += 1
    print("batch: %d decoded, %d skipped -> %s" % (ok, skip, args.out))


def add_parser(sub):
    p = sub.add_parser("tex", help="Xbox 360 tiled DXT texture decode")
    s = p.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("decode", help="decode one texture (by descriptor id) from a .HO")
    a.add_argument("file"); a.add_argument("--id", required=True, type=lambda x: int(x, 16))
    a.add_argument("-o", "--out"); a.add_argument("--opaque", action="store_true",
                                                  help="ignore alpha (for DXT1 opaque-white masks)")
    a.set_defaults(func=_decode)
    a = s.add_parser("batch", help="decode every texture in a .HO to PNGs")
    a.add_argument("file"); a.add_argument("-o", "--out", required=True); a.set_defaults(func=_batch)
