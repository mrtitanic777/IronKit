"""Extract an embedded Scaleform GFx DefineFont3 vector font to a real TTF.

GoodEngine UI fonts live in FONT.ho as DefineFont3 tags: vector glyph outlines (SWF shape
records), a code table (Unicode per glyph) and layout (ascent/descent/advances). We parse
the shapes into quadratic contours and rebuild a TTF with fontTools (DefineFont3 em = 20480).
"""
import struct

from .bits import Bits
from . import gfx

u16 = lambda d, o: struct.unpack_from("<H", d, o)[0]
s16 = lambda d, o: struct.unpack_from("<h", d, o)[0]


def _glyph_contours(data, start):
    """Parse one SWF glyph SHAPE into [[(op, *pts)...]] contours (m/l/q, SWF coords)."""
    br = Bits(data, start)
    nfill, nline = br.read(4), br.read(4)
    x = y = 0
    contours, cur = [], None
    while True:
        if br.read(1) == 0:                               # style-change / end
            f = br.read(5)
            if f == 0:
                break
            if f & 0x01:                                  # moveTo (absolute)
                mb = br.read(5); x = br.reads(mb); y = br.reads(mb)
                if cur:
                    contours.append(cur)
                cur = [("m", x, y)]
            if f & 0x02:
                br.read(nfill)
            if f & 0x04:
                br.read(nfill)
            if f & 0x08:
                br.read(nline)
            if f & 0x10:
                break
        else:                                             # edge
            if br.read(1):                                # straight
                nb = br.read(4) + 2
                if br.read(1):
                    dx, dy = br.reads(nb), br.reads(nb)
                elif br.read(1):
                    dx, dy = 0, br.reads(nb)
                else:
                    dx, dy = br.reads(nb), 0
                x += dx; y += dy; cur.append(("l", x, y))
            else:                                         # quadratic curve
                nb = br.read(4) + 2
                cdx, cdy, adx, ady = br.reads(nb), br.reads(nb), br.reads(nb), br.reads(nb)
                cx, cy = x + cdx, y + cdy; x = cx + adx; y = cy + ady
                cur.append(("q", cx, cy, x, y))
    if cur:
        contours.append(cur)
    return contours


def _find_font_tag(data, font_id):
    for off, mg, body in gfx.find_movies(data):
        for code, d in gfx.parse_tags(body):
            if code == 75 and u16(d, 0) == font_id:
                return d
    return None


def extract(data, font_id, out_path, family=None):
    """Parse DefineFont3 `font_id` from `data` and write a TTF to out_path. Returns (name, glyphs)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fontdata = _find_font_tag(data, font_id)
    if not fontdata:
        raise KeyError("DefineFont3 id=%d not found" % font_id)
    flags = fontdata[2]; nlen = fontdata[4]
    name = fontdata[5:5 + nlen].split(b"\x00")[0].decode("latin1")
    p = 5 + nlen
    n = u16(fontdata, p); p += 2
    ot = p
    wide = flags & 0x08
    if wide:
        offs = [struct.unpack_from("<I", fontdata, ot + 4 * k)[0] for k in range(n + 1)]
    else:
        offs = [u16(fontdata, ot + 2 * k) for k in range(n + 1)]
    shapes = [_glyph_contours(fontdata, ot + offs[g]) for g in range(n)]
    ctp = ot + offs[n]
    codes = [u16(fontdata, ctp + 2 * k) for k in range(n)]
    lp = ctp + 2 * n
    ascent, descent = s16(fontdata, lp), s16(fontdata, lp + 2); lp += 6
    advances = [s16(fontdata, lp + 2 * k) for k in range(n)]

    EM, UPM = 20480, 2048
    sc = lambda v: round(v * UPM / EM)
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder([".notdef"] + ["g%d" % g for g in range(n)])
    glyfs = {".notdef": TTGlyphPen(None).glyph()}
    metrics = {".notdef": (sc(advances[0]) if advances else 600, 0)}
    cmap = {}
    for g in range(n):
        pen = TTGlyphPen(None); open_c = False
        for c in shapes[g]:
            for op in c:
                if op[0] == "m":
                    if open_c:
                        pen.closePath()
                    pen.moveTo((sc(op[1]), -sc(op[2]))); open_c = True
                elif op[0] == "l":
                    pen.lineTo((sc(op[1]), -sc(op[2])))
                else:
                    pen.qCurveTo((sc(op[1]), -sc(op[2])), (sc(op[3]), -sc(op[4])))
            if open_c:
                pen.closePath(); open_c = False
        glyfs["g%d" % g] = pen.glyph()
        metrics["g%d" % g] = (sc(advances[g]), 0)
        if codes[g] and codes[g] not in cmap:
            cmap[codes[g]] = "g%d" % g
    fb.setupGlyf(glyfs)
    fb.setupCharacterMap(cmap)
    fb.setupHorizontalMetrics(metrics)
    asc, dsc = sc(ascent), -abs(sc(descent))
    fb.setupHorizontalHeader(ascent=asc, descent=dsc)
    fam = family or name.replace("-", "")
    fb.setupNameTable({"familyName": fam, "styleName": "Regular", "psName": fam})
    fb.setupOS2(sTypoAscender=asc, sTypoDescender=dsc, usWinAscent=asc, usWinDescent=-dsc)
    fb.setupPost()
    fb.save(out_path)
    return name, n


# ----------------------------------------------------------------------------- CLI
def _list(args):
    data = open(args.file, "rb").read()
    for off, mg, body in gfx.find_movies(data):
        for ver, fid, name, ng, bold, lay in gfx.fonts(body):
            print("DefineFont%d id=%d %-20s glyphs=%d%s" % (ver, fid, repr(name), ng, " bold" if bold else ""))


def _extract(args):
    data = open(args.file, "rb").read()
    name, n = extract(data, args.id, args.out, family=args.family)
    print("extracted %r (%d glyphs) -> %s" % (name, n, args.out))


def add_parser(sub):
    p = sub.add_parser("font", help="extract embedded GFx fonts to TTF")
    s = p.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("list", help="list embedded fonts + ids"); a.add_argument("file"); a.set_defaults(func=_list)
    a = s.add_parser("extract", help="extract a DefineFont3 by id to a TTF")
    a.add_argument("file"); a.add_argument("--id", required=True, type=int)
    a.add_argument("-o", "--out", required=True); a.add_argument("--family")
    a.set_defaults(func=_extract)
