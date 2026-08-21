#!/usr/bin/env python3
"""Extract DOOM picture-format graphics from a WAD and emit RGB565 C arrays."""

import argparse
import struct
import sys
from pathlib import Path

HEADER = struct.Struct("<4sii")
DIRENT = struct.Struct("<ii8s")
PIC    = struct.Struct("<hhhh")

TRANSPARENT = -1


class GfxError(Exception):
    pass


def read_directory(data):
    magic, numlumps, infotableofs = HEADER.unpack_from(data, 0)
    if magic not in (b"IWAD", b"PWAD"):
        raise GfxError(f"bad magic {magic!r}")
    out = {}
    for i in range(numlumps):
        pos, size, raw = DIRENT.unpack_from(data, infotableofs + i * DIRENT.size)
        out.setdefault(raw.rstrip(b"\0").decode("ascii", "replace").upper(), (pos, size))
    return out


def read_palette(data, pos):
    return [struct.unpack_from("<BBB", data, pos + i * 3) for i in range(256)]


def read_picture(data, pos, size):
    """Doom's column-post format. Returns (w, h, xoff, yoff, pixels) with
    pixels[y][x] a palette index or TRANSPARENT."""
    w, h, xoff, yoff = PIC.unpack_from(data, pos)
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        raise GfxError(f"implausible picture size {w}x{h}")
    cols = [struct.unpack_from("<I", data, pos + 8 + c * 4)[0] for c in range(w)]
    px = [[TRANSPARENT] * w for _ in range(h)]
    for c, coff in enumerate(cols):
        p = pos + coff
        while True:
            top = data[p]
            if top == 0xFF:
                break
            ln = data[p + 1]
            p += 3
            for i in range(ln):
                y = top + i
                if 0 <= y < h:
                    px[y][c] = data[p + i]
            p += ln + 1
    return w, h, xoff, yoff, px


def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def to565(pic, pal, key):
    w, h, _, _, px = pic
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            v = px[y][x]
            row.append(key if v == TRANSPARENT else rgb565(*pal[v]))
        out.append(row)
    return out


def scale(img, num, den):
    """nearest-neighbour, num/den"""
    h, w = len(img), len(img[0])
    nh, nw = h * num // den, w * num // den
    return [[img[y * den // num][x * den // num] for x in range(nw)] for y in range(nh)]


def crop_opaque(img, key):
    h, w = len(img), len(img[0])
    xs = [x for y in range(h) for x in range(w) if img[y][x] != key]
    ys = [y for y in range(h) for x in range(w) if img[y][x] != key]
    if not xs:
        raise GfxError("sprite is entirely transparent")
    return [row[min(xs):max(xs)+1] for row in img[min(ys):max(ys)+1]]


def pick_key(pal):
    used = {rgb565(*c) for c in pal}
    for v in range(0xFFFF, 0, -1):
        if v not in used:
            return v
    raise GfxError("no free RGB565 value for the transparency key")


def classify(img, key, cell=8):
    """0 = fully transparent, 1 = mixed, 2 = fully opaque"""
    h, w = len(img), len(img[0])
    out = []
    for cy in range(h // cell):
        row = []
        for cx in range(w // cell):
            n = sum(1 for r in range(cell) for c in range(cell)
                    if img[cy*cell+r][cx*cell+c] != key)
            row.append(2 if n == cell*cell else (0 if n == 0 else 1))
        out.append(row)
    return out


def pad_to_cell(img, key, cell=8):
    """pad with the key colour so the image lands on whole cell boundaries"""
    h, w = len(img), len(img[0])
    nw = (w + cell - 1) // cell * cell
    nh = (h + cell - 1) // cell * cell
    left = (nw - w) // 2
    top = nh - h
    out = [[key] * nw for _ in range(nh)]
    for y in range(h):
        for x in range(w):
            out[top + y][left + x] = img[y][x]
    return out


def emit(out, name, img):
    h, w = len(img), len(img[0])
    out.write(f"#define {name}_W {w}\n#define {name}_H {h}\n")
    out.write(f"static const uint16_t {name}[{w*h}] = {{\n")
    flat = [v for row in img for v in row]
    for i in range(0, len(flat), 12):
        out.write("    " + "".join(f"0x{v:04X}," for v in flat[i:i+12]) + "\n")
    out.write("};\n\n")
    return w * h * 2


def blit(dst, src, x0, y0, key):
    for y, row in enumerate(src):
        for x, v in enumerate(row):
            if v == key:
                continue
            if 0 <= y0 + y < len(dst) and 0 <= x0 + x < len(dst[0]):
                dst[y0 + y][x0 + x] = v


def right_align(dst, digits, value, xright, y, key):
    """draw an integer right-aligned so its last column lands on xright"""
    s = str(value)
    w = sum(len(digits[int(c)][0]) for c in s)
    x = xright - w
    for c in s:
        g = digits[int(c)]
        blit(dst, g, x, y, key)
        x += len(g[0])
    return x


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wad")
    ap.add_argument("-o", "--output", help="output .h file (default: stdout)")
    ap.add_argument("--gun", default="PISGA0", help="weapon sprite lump (default: PISGA0)")
    ap.add_argument("--bar-num", type=int, default=3,
                    help="scale STBAR by bar-num/bar-den (default 3/4, 320 -> 240)")
    ap.add_argument("--bar-den", type=int, default=4)
    ap.add_argument("--gun-num", type=int, default=3,
                    help="scale the weapon by gun-num/gun-den (default 3/2)")
    ap.add_argument("--gun-den", type=int, default=2)
    ap.add_argument("--ammo", type=int, default=50)
    ap.add_argument("--health", type=int, default=100)
    ap.add_argument("--armor", type=int, default=0)
    ap.add_argument("--key", default=None,
                    help="RGB565 transparency sentinel (default: an unused colour)")
    args = ap.parse_args(argv)

    try:
        data = Path(args.wad).read_bytes()
        d = read_directory(data)
        for need in ("PLAYPAL", args.gun, "STTNUM0", "STTPRCNT"):
            if need not in d:
                raise GfxError(f"{need} not in this WAD")
        pal = read_palette(data, d["PLAYPAL"][0])
        key = int(args.key, 0) if args.key else pick_key(pal)

        gun = crop_opaque(to565(read_picture(data, *d[args.gun]), pal, key), key)
        gun = scale(gun, args.gun_num, args.gun_den)

        digits_full = []
        for i in range(10):
            nm = f"STTNUM{i}"
            if nm not in d:
                raise GfxError(f"{nm} not in this WAD")
            digits_full.append(to565(read_picture(data, *d[nm]), pal, key))
        pct = to565(read_picture(data, *d["STTPRCNT"]), pal, key)

        bar = to565(read_picture(data, *d["STBAR"]), pal, key)
        right_align(bar, digits_full, args.ammo,   44, 3, key)
        x = right_align(bar, digits_full, args.health, 90, 3, key)
        blit(bar, pct, 90, 3, key)
        right_align(bar, digits_full, args.armor, 221, 3, key)
        blit(bar, pct, 221, 3, key)
        bar = scale(bar, args.bar_num, args.bar_den)
    except (OSError, GfxError, struct.error) as e:
        print(f"wadgfx.py: {e}", file=sys.stderr)
        return 1

    f = open(args.output, "w") if args.output else sys.stdout
    f.write(f"/* Generated by wadgfx.py from {Path(args.wad).name} -- do not edit. */\n")
    f.write("#ifndef WADGFX_H\n#define WADGFX_H\n\n#include <stdint.h>\n\n")
    f.write(f"#define GFX_KEY 0x{key:04X}\n\n")
    gun = pad_to_cell(gun, key)
    cls = classify(gun, key)
    total = emit(f, "gfx_gun", gun)
    total += emit(f, "gfx_bar", bar)
    f.write(f"#define GUN_CW {len(cls[0])}\n#define GUN_CH {len(cls)}\n")
    f.write(f"static const uint8_t gfx_gun_cell[{len(cls)}][{len(cls[0])}] = {{\n")
    for row in cls:
        f.write("    {" + ",".join(str(v) for v in row) + "},\n")
    f.write("};\n\n")
    total += len(cls) * len(cls[0])
    n = {0:0,1:0,2:0}
    for row in cls:
        for v in row: n[v] += 1
    f.write(f"/* cells: {n[2]} opaque, {n[1]} mixed, {n[0]} empty */\n\n")
    f.write("#endif\n")
    if args.output:
        f.close()
    print(f"gun {len(gun[0])}x{len(gun)}, bar {len(bar[0])}x{len(bar)}, "
          f"{total} bytes of RGB565", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())