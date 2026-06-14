"""Scaleform GFx movie parsing.

GFx is an SWF-derived UI format ("GFX"/"CFX" magic, SWF-style tag stream). In GoodEngine
archives each movie is wrapped (engine header + movie name + u64 asset-IDs, then the GFX
body). This module finds movies inside any file and reads their external images
(DefineExternalImage), embedded fonts (DefineFont2/3), and placements (PlaceObject2/3).
"""
import re
import struct
import zlib

from .bits import Bits

u16 = lambda d, o: struct.unpack_from("<H", d, o)[0]
u32 = lambda d, o: struct.unpack_from("<I", d, o)[0]


def find_movies(data):
    """Yield (offset, magic, body) for each GFx/CFX movie embedded in data."""
    for m in re.finditer(rb"[CG]F[XC]", data):
        i = m.start()
        if data[i:i + 3] not in (b"GFX", b"CFX", b"GFC"):
            continue
        flen = u32(data, i + 4)
        body = data[i + 8:i + 8 + flen]
        if data[i:i + 3] == b"CFX":
            try:
                body = zlib.decompress(body)
            except Exception:
                continue
        yield i, data[i:i + 3].decode("latin1"), body


def wrapper_name(data, gfx_off):
    """Best-effort movie name from the GoodEngine wrapper preceding the GFX magic."""
    start = max(0, gfx_off - 512)
    chunk = data[start:gfx_off]
    names = re.findall(rb"[\x20-\x7e]{3,}", chunk)
    return names[-1].decode("latin1") if names else ""


def parse_tags(body):
    br = Bits(body, 0)
    nb = br.read(5)
    for _ in range(4):
        br.read(nb)
    br.align()
    return read_tags(body, br.p + 4)          # skip frameRate(u16) + frameCount(u16)


def read_tags(body, p):
    tags = []
    while p + 2 <= len(body):
        tc = u16(body, p); p += 2
        code, length = tc >> 6, tc & 0x3F
        if length == 0x3F:
            length = u32(body, p); p += 4
        tags.append((code, body[p:p + length])); p += length
        if code == 0:
            break
    return tags


def ext_images(body):
    """DefineExternalImage (1001/1008/1009): (charId, w, h, name)."""
    out = []
    for code, d in parse_tags(body):
        if code in (1001, 1008, 1009) and len(d) >= 10:
            cid, w, h = u16(d, 0), u16(d, 4), u16(d, 6)
            nlen = d[8]
            out.append((cid, w, h, d[9:9 + nlen].decode("latin1", "replace")))
    return out


def fonts(body):
    """DefineFont2(48)/DefineFont3(75): (tagCode, fontId, name, numGlyphs, bold, hasLayout)."""
    out = []
    for code, d in parse_tags(body):
        if code in (48, 75) and len(d) >= 6:
            fid, flags, nlen = u16(d, 0), d[2], d[4]
            name = d[5:5 + nlen].split(b"\x00")[0].decode("latin1", "replace")
            ng = u16(d, 5 + nlen)
            out.append((code, fid, name, ng, bool(flags & 1), bool(flags & 0x80)))
    return out


def parse_matrix(br):
    """SWF MATRIX -> (scaleX, scaleY, translateX_px, translateY_px)."""
    sx = sy = 1.0
    if br.read(1):
        n = br.read(5); sx = br.read(n) / 65536.0; sy = br.read(n) / 65536.0
    if br.read(1):
        n = br.read(5); br.read(n); br.read(n)
    n = br.read(5)
    tx = br.reads(n); ty = br.reads(n)
    return sx, sy, tx / 20.0, ty / 20.0


def placements(body):
    """PlaceObject2(26)/PlaceObject3(70): (depth, charId, matrix)."""
    out = []
    for code, d in parse_tags(body):
        if code not in (26, 70):
            continue
        try:
            flags = d[0]; p = 2 if code == 70 else 1
            depth = u16(d, p); p += 2
            cid = None
            if flags & 0x02:
                cid = u16(d, p); p += 2
            mat = parse_matrix(Bits(d, p)) if flags & 0x04 else None
            out.append((depth, cid, mat))
        except Exception:
            pass
    return out


# ----------------------------------------------------------------------------- CLI
def _each(args):
    data = open(args.file, "rb").read()
    movies = list(find_movies(data))
    if not movies:
        print("no GFx movies found in", args.file)
    return data, movies


def _scan(args):
    data, movies = _each(args)
    for off, mg, body in movies:
        nm = wrapper_name(data, off)
        imgs = ext_images(body); fns = fonts(body)
        print("%s @0x%X  %-28s images=%d fonts=%d" % (mg, off, nm, len(imgs), len(fns)))


def _images(args):
    data, movies = _each(args)
    for off, mg, body in movies:
        nm = wrapper_name(data, off)
        for cid, w, h, name in ext_images(body):
            print("%-28s cid=%-4d %4dx%-4d %s" % (nm, cid, w, h, name))


def _fonts(args):
    data, movies = _each(args)
    for off, mg, body in movies:
        nm = wrapper_name(data, off)
        for code, fid, name, ng, bold, lay in fonts(body):
            print("%-24s DefineFont%d id=%d %-20s glyphs=%d%s%s"
                  % (nm, code, fid, repr(name), ng, " bold" if bold else "", " layout" if lay else ""))


def _placements(args):
    data, movies = _each(args)
    for off, mg, body in movies:
        nm = wrapper_name(data, off)
        imgc = {cid: name for cid, w, h, name in ext_images(body)}
        for depth, cid, mat in placements(body):
            if cid in imgc and mat:
                sx, sy, tx, ty = mat
                print("%-24s depth=%-3d %-22s pos=(%.1f,%.1f) scale=(%.3f,%.3f)"
                      % (nm, depth, imgc[cid], tx, ty, sx, sy))


def add_parser(sub):
    p = sub.add_parser("gfx", help="Scaleform GFx movies")
    s = p.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in [
        ("scan", _scan, "list GFx movies in a file"),
        ("images", _images, "list external images (cid/dims/name)"),
        ("fonts", _fonts, "list embedded fonts"),
        ("placements", _placements, "list image placements (depth/pos/scale)"),
    ]:
        a = s.add_parser(name, help=helptext); a.add_argument("file"); a.set_defaults(func=fn)
