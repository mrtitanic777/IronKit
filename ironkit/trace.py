"""Decode UP_TRACE_GFX traces emitted by the recompiled port into screen layouts.

The port (patched ReXGlue) logs, per draw: bound texture (dims/base), viewport, scissor,
vertex-shader transform constants (c0..c7, hex), and the vertex buffer (int16 positions,
stride 12, big-endian). The vertex shader computes  clip = c-matrix * (vx,vy,0,1)  then the
viewport maps clip -> screen; we replay that to recover the exact on-screen rect of every
texture. Frames are timestamped, so we can also segment the trace into distinct screens.
"""
import re
import struct

FRM = re.compile(r"=== FRAME (\d+) t=(\d+)ms ===")
TEX = re.compile(r"T:(\d+)x(\d+)@([0-9A-Fa-f]+)\.f(\d+)")
VP = re.compile(r"VP:(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)")
CHEX = re.compile(r" C:([0-9A-Fa-f]+)")
VB = re.compile(r"VB(\d+)@([0-9A-Fa-f]+)/([0-9A-Fa-f]+):([0-9A-Fa-f]*)")


def _f(h8):
    return struct.unpack("<f", struct.pack("<I", int(h8, 16)))[0]


def textures(line):
    return [(int(w), int(h), b.upper(), int(f)) for w, h, b, f in TEX.findall(line)]


def _consts(line):
    m = CHEX.search(line)
    if not m:
        return None
    h = m.group(1)
    return [_f(h[i:i + 8]) for i in range(0, min(len(h), 256), 8)]


def _verts(line):
    pts = []
    for sl, ad, sz, by in VB.findall(line):
        raw = bytes.fromhex(by)
        for o in range(0, len(raw) - 11, 12):              # stride 12, pos = first 2 BE int16
            pts.append(struct.unpack_from(">hh", raw, o))
    return pts


def screen_rect(line):
    """Exact on-screen (x0,y0,x1,y1) for a full-screen-pass draw, or None."""
    c = _consts(line); vp = VP.search(line); pts = _verts(line)
    if not c or not vp or len(c) < 8 or not pts:
        return None
    xs, xo, ys, yo = map(float, vp.groups())
    sx, sy = [], []
    for vx, vy in pts:
        cx = c[0] * vx + c[1] * vy + c[3]
        cy = c[4] * vx + c[5] * vy + c[7]
        sx.append(cx * xs + xo); sy.append(cy * ys + yo)
    return min(sx), min(sy), max(sx), max(sy)


def _frames(path, tail_bytes=None, fs_x=640.0, fs_tol=60.0):
    """Yield (frame, t_ms, {base:(w,h,rect)}) per frame. tail_bytes limits to the file's end;
    fs_x/fs_tol set the viewport-xscale window used to recognise a full-screen pass (a rect is
    only solved for those). Defaults match a 1280-wide target (xscale ~= 640)."""
    import os
    start = 0
    if tail_bytes:
        start = max(0, os.path.getsize(path) - tail_bytes)
    cur_f = cur_t = 0; cur = {}
    with open(path, encoding="latin1", errors="replace") as fh:
        if start:
            fh.seek(start); fh.readline()
        for line in fh:
            if line[:1] == "=":
                m = FRM.match(line)
                if m:
                    if cur:
                        yield cur_f, cur_t, cur
                    cur_f, cur_t, cur = int(m.group(1)), int(m.group(2)), {}
                continue
            if line[:1] != "D":
                continue
            vp = VP.search(line); fs = vp and abs(float(vp.group(1)) - fs_x) < fs_tol
            for w, h, base, fmt in textures(line):
                if base not in cur:
                    cur[base] = (w, h, screen_rect(line) if fs else None)
        if cur:
            yield cur_f, cur_t, cur


def segment_screens(path, min_ms=500, jaccard=0.45, fs_x=640.0, fs_tol=60.0):
    """Group frames into distinct screens by texture-set similarity."""
    segs = []
    for f, t, bs in _frames(path, fs_x=fs_x, fs_tol=fs_tol):
        keys = set(bs)
        if not keys:
            continue
        if segs and len(keys & segs[-1]["keys"]) / max(1, len(keys | segs[-1]["keys"])) > jaccard:
            s = segs[-1]; s["t1"], s["f1"], s["keys"], s["bs"], s["n"] = t, f, keys, bs, s["n"] + 1
        else:
            segs.append({"t0": t, "t1": t, "f0": f, "f1": f, "keys": keys, "bs": bs, "n": 1})
    return [s for s in segs if s["t1"] - s["t0"] >= min_ms]


# ----------------------------------------------------------------------------- CLI
def _screens(args):
    for s in segment_screens(args.file, fs_x=args.fs_x, fs_tol=args.fs_tol):
        print("=== screen  %.1f - %.1fs  (%d frames) ===" % (s["t0"] / 1000, s["t1"] / 1000, s["n"]))
        for base, (w, h, r) in sorted(s["bs"].items(), key=lambda kv: -(kv[1][0] * kv[1][1])):
            if w * h < 64 * 64:
                continue
            rect = "(%5.0f,%-5.0f %4.0fx%-4.0f)" % (r[0], r[1], r[2] - r[0], r[3] - r[1]) if r else "cache/RT"
            print("   %4dx%-4d base=%-9s %s" % (w, h, base, rect))
        print()


def _solve(args):
    """Element rects on the last frame that draws a `dims` texture (default the 512x512 house)."""
    want = tuple(int(x) for x in args.dims.split("x"))
    frames = list(_frames(args.file, tail_bytes=int(args.tail * 1024 * 1024),
                          fs_x=args.fs_x, fs_tol=args.fs_tol))
    chosen = None
    for f, t, bs in reversed(frames):
        if any((w, h) == want for w, h, _ in bs.values()):
            chosen = (f, t, bs); break
    if not chosen:
        print("no frame with a %dx%d texture" % want); return
    f, t, bs = chosen
    print("frame %d @ %.1fs:" % (f, t / 1000))
    for base, (w, h, r) in sorted(bs.items(), key=lambda kv: -(kv[1][0] * kv[1][1])):
        rect = "(%5.0f,%-5.0f %4.0fx%-4.0f)" % (r[0], r[1], r[2] - r[0], r[3] - r[1]) if r else "cache/RT"
        print("   %4dx%-4d base=%-9s %s" % (w, h, base, rect))


def _fs_args(a):
    a.add_argument("--fs-x", type=float, default=640.0,
                   help="viewport xscale of a full-screen pass (default 640 = 1280-wide target)")
    a.add_argument("--fs-tol", type=float, default=60.0, help="tolerance around --fs-x")


def add_parser(sub):
    p = sub.add_parser("trace", help="decode ReXGlue UP_TRACE_GFX traces into layouts")
    s = p.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("screens", help="segment the trace into distinct screens + their textures")
    a.add_argument("file"); _fs_args(a); a.set_defaults(func=_screens)
    a = s.add_parser("solve", help="exact element rects on a chosen frame")
    a.add_argument("file"); a.add_argument("--dims", default="512x512",
                                           help="pick the last frame that draws this texture size")
    a.add_argument("--tail", type=float, default=16.0, help="MB from the end of the log to scan")
    _fs_args(a); a.set_defaults(func=_solve)
