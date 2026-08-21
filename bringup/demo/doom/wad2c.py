#!/usr/bin/env python3
"""Extract a Doom map's VERTEXES and LINEDEFS from a WAD and emit them as C arrays.

    python3 wad2c.py DOOM1.WAD --map E1M1 -o e1m1_map.h
    python3 wad2c.py DOOM1.WAD --map E1M1 --skip-twosided --prebake -o e1m1_walls.h
"""

import argparse
import struct
import sys
from math import gcd
from pathlib import Path

HEADER  = struct.Struct("<4sii")
DIRENT  = struct.Struct("<ii8s")
VERTEX  = struct.Struct("<hh")
LINEDEF = struct.Struct("<HHHHHHH")
SEG     = struct.Struct("<HHhHhh")
SSECTOR = struct.Struct("<HH")
NODE    = struct.Struct("<hhhh8hHH")
SIDEDEF = struct.Struct("<hh8s8s8sH")
SECTOR  = struct.Struct("<hh8s8shhh")

NF_SUBSECTOR = 0x8000

NO_SIDEDEF = 0xFFFF

MAP_LUMPS = {
    "THINGS", "LINEDEFS", "SIDEDEFS", "VERTEXES", "SEGS", "SSECTORS",
    "NODES", "SECTORS", "REJECT", "BLOCKMAP", "BEHAVIOR",
}


class WadError(Exception):
    pass


def lump_name(raw):
    return raw.split(b"\0", 1)[0].decode("ascii", "replace").upper()


def read_directory(data):
    if len(data) < HEADER.size:
        raise WadError("file is too small to be a WAD")
    magic, numlumps, infotableofs = HEADER.unpack_from(data, 0)
    if magic not in (b"IWAD", b"PWAD"):
        raise WadError(f"bad magic {magic!r}: not an IWAD or PWAD")
    if numlumps < 0 or infotableofs < 0:
        raise WadError("negative lump count or directory offset")
    end = infotableofs + numlumps * DIRENT.size
    if end > len(data):
        raise WadError(f"directory runs past EOF (needs {end} bytes, file is {len(data)})")

    directory = []
    for i in range(numlumps):
        filepos, size, raw = DIRENT.unpack_from(data, infotableofs + i * DIRENT.size)
        if size < 0 or filepos < 0 or filepos + size > len(data):
            raise WadError(f"lump {i} ({lump_name(raw)}) points outside the file")
        directory.append((lump_name(raw), filepos, size))
    return magic.decode(), directory


def find_map_lumps(directory, mapname):
    mapname = mapname.upper()
    for i, (name, _, size) in enumerate(directory):
        if name != mapname or size != 0:
            continue
        lumps = {}
        for name2, pos2, size2 in directory[i + 1:]:
            if name2 not in MAP_LUMPS:
                break
            lumps.setdefault(name2, (pos2, size2))
        if "BEHAVIOR" in lumps:
            raise WadError(f"{mapname} is a Hexen-format map; only Doom format is supported")
        return lumps
    raise WadError(f"no map marker named {mapname} in this WAD")


def parse_vertexes(data, pos, size):
    if size % VERTEX.size:
        raise WadError(f"VERTEXES size {size} is not a multiple of {VERTEX.size}")
    return [VERTEX.unpack_from(data, pos + o) for o in range(0, size, VERTEX.size)]


def parse_linedefs(data, pos, size, nvertexes):
    if size % LINEDEF.size:
        raise WadError(f"LINEDEFS size {size} is not a multiple of {LINEDEF.size}")
    lines = []
    for o in range(0, size, LINEDEF.size):
        v1, v2, flags, _special, _tag, right, left = LINEDEF.unpack_from(data, pos + o)
        if v1 >= nvertexes or v2 >= nvertexes:
            raise WadError(f"linedef {o // LINEDEF.size} references vertex {max(v1, v2)} "
                           f"but the map only has {nvertexes}")
        two_sided = left != NO_SIDEDEF and right != NO_SIDEDEF
        lines.append((v1, v2, flags, two_sided))
    return lines


def linedef_normals(vertexes, linedefs):
    normals = []
    for v1, v2, _flags, _two_sided in linedefs:
        x1, y1 = vertexes[v1]
        x2, y2 = vertexes[v2]
        nx = -(y2 - y1)
        ny = (x2 - x1)
        g = gcd(abs(nx), abs(ny))
        if g:
            nx //= g
            ny //= g
        normals.append((nx, ny))
    return normals


def parse_segs(data, pos, size, nvertexes):
    if size % SEG.size:
        raise WadError(f"SEGS size {size} is not a multiple of {SEG.size}")
    segs = []
    for o in range(0, size, SEG.size):
        v1, v2, _angle, linedef, side, _off = SEG.unpack_from(data, pos + o)
        if v1 >= nvertexes or v2 >= nvertexes:
            raise WadError(f"seg {o // SEG.size} references vertex {max(v1, v2)} "
                           f"but the map only has {nvertexes}")
        segs.append((v1, v2, linedef, side))
    return segs


def parse_ssectors(data, pos, size, nsegs):
    if size % SSECTOR.size:
        raise WadError(f"SSECTORS size {size} is not a multiple of {SSECTOR.size}")
    subs = []
    for o in range(0, size, SSECTOR.size):
        count, first = SSECTOR.unpack_from(data, pos + o)
        if count == 0 or first + count > nsegs:
            raise WadError(f"subsector {o // SSECTOR.size} spans segs "
                           f"{first}..{first + count - 1} but the map only has {nsegs}")
        subs.append((first, count))
    return subs


def linedef_flag_bytes(linedefs):
    out = []
    dropped = 0
    for _v1, _v2, flags, two_sided in linedefs:
        if flags & 0xFF00:
            dropped += 1
        b = flags & 0x00FF
        b = (b | 0x04) if two_sided else (b & ~0x04)
        out.append(b)
    return out, dropped


def parse_nodes(data, pos, size, nsubs):
    if size % NODE.size:
        raise WadError(f"NODES size {size} is not a multiple of {NODE.size}")
    count = size // NODE.size
    nodes = []
    for o in range(0, size, NODE.size):
        rec = NODE.unpack_from(data, pos + o)
        x, y, dx, dy = rec[0], rec[1], rec[2], rec[3]
        right, left = rec[12], rec[13]
        for child in (right, left):
            if child & NF_SUBSECTOR:
                if (child & 0x7FFF) >= nsubs:
                    raise WadError(f"node {o // NODE.size} points at subsector "
                                   f"{child & 0x7FFF} but the map has {nsubs}")
            elif child >= count:
                raise WadError(f"node {o // NODE.size} points at node {child} "
                               f"but the map has {count}")
        nodes.append((x, y, dx, dy, right, left))
    return nodes


def parse_sidedefs(data, pos, size, nsectors):
    if size % SIDEDEF.size:
        raise WadError(f"SIDEDEFS size {size} is not a multiple of {SIDEDEF.size}")
    sides = []
    for o in range(0, size, SIDEDEF.size):
        *_ , sector = SIDEDEF.unpack_from(data, pos + o)
        if sector >= nsectors:
            raise WadError(f"sidedef {o // SIDEDEF.size} references sector {sector} "
                           f"but the map only has {nsectors}")
        sides.append(sector)
    return sides


def parse_sectors(data, pos, size):
    if size % SECTOR.size:
        raise WadError(f"SECTORS size {size} is not a multiple of {SECTOR.size}")
    out = []
    for o in range(0, size, SECTOR.size):
        floor, ceil, *_ = SECTOR.unpack_from(data, pos + o)
        out.append((floor, ceil))
    return out


def parse_linedef_sides(data, pos, size):
    out = []
    for o in range(0, size, LINEDEF.size):
        _v1, _v2, _fl, _sp, _tag, right, left = LINEDEF.unpack_from(data, pos + o)
        out.append((right, left))
    return out


def build_seg_heights(segs, linesides, sidedefs, sectors):
    NOBACK = -32768
    out = []
    for si, (_v1, _v2, ld, side) in enumerate(segs):
        right, left = linesides[ld]
        fs = right if side == 0 else left
        bs = left if side == 0 else right
        if fs == NO_SIDEDEF:
            raise WadError(f"seg {si} has no front sidedef")
        ffloor, fceil = sectors[sidedefs[fs]]
        if bs == NO_SIDEDEF:
            bfloor = bceil = NOBACK
        else:
            bfloor, bceil = sectors[sidedefs[bs]]
        out.append((fceil, ffloor, bceil, bfloor))
    return out


def build_subsectors(vertexes, segs, ssectors, normals, lineflags, k):
    out_subs = []
    out_segs = []
    for first, count in ssectors:
        pts = []
        for j in range(first, first + count):
            pts.append(vertexes[segs[j][0]])
            pts.append(vertexes[segs[j][1]])
        ox = (min(p[0] for p in pts) + max(p[0] for p in pts)) // 2
        oy = (min(p[1] for p in pts) + max(p[1] for p in pts)) // 2

        base = len(out_segs)
        for j in range(first, first + count):
            rx1, ry1 = vertexes[segs[j][0]]
            rx2, ry2 = vertexes[segs[j][1]]
            nx, ny = normals[segs[j][2]]
            if segs[j][3]:
                nx, ny = -nx, -ny
            x1 = (rx1 - ox) >> k
            y1 = (ry1 - oy) >> k
            x2 = (rx2 - ox) >> k
            y2 = (ry2 - oy) >> k
            out_segs.append((x1, y1, x2, y2, nx, ny,
                             nx * x1 + ny * y1, lineflags[segs[j][2]]))
        out_subs.append((ox, oy, base, count))
    return out_subs, out_segs


def check_widths(subs, segs):
    coords = [c for s in segs for c in s[:4]]
    norms = [c for s in segs for c in s[4:6]]
    ds = [s[6] for s in segs]
    origins = [c for s in subs for c in s[:2]]
    for name, vals, bits in (("local coord", coords, 8), ("normal", norms, 8),
                             ("local D", ds, 16), ("origin", origins, 16)):
        lo, hi = min(vals), max(vals)
        if not fits(lo, hi, bits):
            raise WadError(f"{name} range [{lo}, {hi}] does not fit int{bits}; "
                           "raise --scale-coords")
    if not all(0 <= s[7] <= 255 for s in segs):
        raise WadError("seg flag byte out of range")


def report_subsectors(subs, segs, stream):
    coord_peak = max(abs(c) for s in segs for c in s[:4])
    d_peak = max(abs(s[6]) for s in segs)

    side_peak = 0
    for ox, oy, base, count in subs:
        group = segs[base:base + count]
        xs = [c for s in group for c in (s[0], s[2])]
        ys = [c for s in group for c in (s[1], s[3])]
        corners = [(x, y) for x in (min(xs), max(xs)) for y in (min(ys), max(ys))]
        for _x1, _y1, _x2, _y2, nx, ny, d, _flags in group:
            for px, py in corners:
                side_peak = max(side_peak, abs(nx * px + ny * py - d))

    w = stream.write
    w(f"\nsubsector bake over {len(subs)} subsectors / {len(segs)} segs\n")
    w(f"  max |local coord|      {coord_peak:>8}   {'int8' if coord_peak <= 127 else 'OVERFLOW'}\n")
    w(f"  max |local D|          {d_peak:>8}   {'int16' if d_peak <= 32767 else 'OVERFLOW'}\n")
    w(f"  max |side test|        {side_peak:>8}   {'int16' if side_peak <= 32767 else 'OVERFLOW'}\n\n")


def emit_subsectors_c(out, wadpath, mapname, subs, segs, k, columns,
                      nodes=None, heights=None):
    ident = c_ident(mapname)
    guard = f"{ident.upper()}_SUBSECTORS_H"
    w = out.write
    w(f"/* Generated by wad2c.py from {Path(wadpath).name} -- do not edit. */\n")
    w(f"#ifndef {guard}\n#define {guard}\n\n#include <stdint.h>\n\n")

    w("#ifndef DOOM_SUBSECTOR_TYPES_H\n#define DOOM_SUBSECTOR_TYPES_H\n")
    w("typedef struct { int16_t ox, oy; uint16_t first_seg, num_segs; } map_subsector_t;\n")
    w("typedef struct { int8_t x1, y1, x2, y2, nx, ny;\n")
    w("                 uint8_t flags; int16_t d; } map_seg_t;\n")
    if nodes is not None:
        w("typedef struct { int16_t x, y, dx, dy;\n")
        w("                 uint16_t right, left; } map_node_t;\n")
        w("#define NF_SUBSECTOR     0x8000\n")
    if heights is not None:
        w("typedef struct { int16_t fceil, ffloor, bceil, bfloor; } seg_height_t;\n")
        w("#define SEG_NOBACK       (-32768)\n")
    w("#define ML_BLOCKING      0x01\n")
    w("#define ML_BLOCKMONSTERS 0x02\n")
    w("#define ML_TWOSIDED      0x04\n")
    w("#define ML_DONTPEGTOP    0x08\n")
    w("#define ML_DONTPEGBOTTOM 0x10\n")
    w("#define ML_SECRET        0x20\n")
    w("#define ML_SOUNDBLOCK    0x40\n")
    w("#define ML_DONTDRAW      0x80\n")
    w("#endif\n\n")

    w(f"#define {ident.upper()}_SCALE_SHIFT ({k})\n")
    w(f"#define {ident.upper()}_NUM_SUBSECTORS {len(subs)}\n")
    w(f"#define {ident.upper()}_NUM_SEGS {len(segs)}\n")
    w(f"#define {ident.upper()}_MAX_ABS_COORD ({max(abs(c) for s in segs for c in s[:4])})\n")
    w(f"#define {ident.upper()}_MAX_ABS_D ({max(abs(s[6]) for s in segs)})\n")
    if nodes is not None:
        w(f"#define {ident.upper()}_NUM_NODES {len(nodes)}\n")
        w(f"#define {ident.upper()}_BSP_ROOT ({len(nodes) - 1})\n")
    w("\n")

    w(f"static const map_subsector_t {ident}_subsectors[{len(subs)}] = {{\n")
    for ox, oy, base, count in subs:
        w(f"    {{{ox:6d},{oy:6d}, {base:4d},{count:3d}}},\n")
    w("};\n\n")

    w(f"static const map_seg_t {ident}_segs[{len(segs)}] = {{\n")
    for x1, y1, x2, y2, nx, ny, d, flags in segs:
        w(f"    {{{x1:4d},{y1:4d},{x2:4d},{y2:4d}, {nx:4d},{ny:4d}, "
          f"0x{flags:02X},{d:6d}}},\n")
    w("};\n\n")

    if heights is not None:
        w(f"static const seg_height_t {ident}_seg_h[{len(heights)}] = {{\n")
        for fceil, ffloor, bceil, bfloor in heights:
            bc = "SEG_NOBACK" if bceil == -32768 else f"{bceil:6d}"
            bf = "SEG_NOBACK" if bfloor == -32768 else f"{bfloor:6d}"
            w(f"    {{{fceil:6d},{ffloor:6d},{bc:>10s},{bf:>10s}}},\n")
        w("};\n\n")

    if nodes is not None:
        w(f"static const map_node_t {ident}_nodes[{len(nodes)}] = {{\n")
        for x, y, dx, dy, right, left in nodes:
            w(f"    {{{x:6d},{y:6d},{dx:6d},{dy:6d}, 0x{right:04X},0x{left:04X}}},\n")
        w("};\n\n")

    w("#endif\n")


def bake(vertexes, linedefs, reduce_normals=False, geom=None):
    geom = geom if geom is not None else vertexes
    baked = []
    for v1, v2, _flags, _two_sided in linedefs:
        gx1, gy1 = geom[v1]
        gx2, gy2 = geom[v2]
        x1, y1 = vertexes[v1]
        nx = -(gy2 - gy1)
        ny = (gx2 - gx1)
        if reduce_normals:
            g = gcd(abs(nx), abs(ny))
            if g:
                nx //= g
                ny //= g
        d = nx * x1 + ny * y1
        baked.append((nx, ny, d))
    return baked


def c_int_type(lo, hi):
    for bits in (8, 16, 32):
        if fits(lo, hi, bits):
            return f"int{bits}_t"
    return "int64_t"


def reduced(baked):
    out = []
    for nx, ny, _d in baked:
        g = gcd(abs(nx), abs(ny)) or 1
        out.append((nx // g, ny // g))
    return out


def fits(lo, hi, bits):
    return -(1 << (bits - 1)) <= lo and hi <= (1 << (bits - 1)) - 1


def report_residuals(baked, vertexes, linedefs, stream):
    worst = 0
    nonzero = collapsed = 0
    for (nx, ny, d), (v1, v2, _f, _t) in zip(baked, linedefs):
        x2, y2 = vertexes[v2]
        r = abs(nx * x2 + ny * y2 - d)
        if r:
            nonzero += 1
            worst = max(worst, r)
        if vertexes[v1] == vertexes[v2]:
            collapsed += 1
    w = stream.write
    w(f"\nquantization cost at this shift\n")
    w(f"  far endpoint off-plane: {nonzero}/{len(baked)} lines, worst residual {worst}\n")
    w(f"  lines collapsed to zero length: {collapsed}\n\n")


def report_ranges(baked, vertexes, linedefs, stream, scaled=False):
    nxs = [b[0] for b in baked]
    nys = [b[1] for b in baked]
    ds  = [b[2] for b in baked]

    w = stream.write
    w(f"\nprebake ranges over {len(baked)} lines\n")
    w(f"  {'field':6s} {'min':>12s} {'max':>12s} {'|max|':>12s}  {'fits':>6s}\n")
    for name, vals in (("nx", nxs), ("ny", nys), ("D", ds)):
        lo, hi = min(vals), max(vals)
        peak = max(abs(lo), abs(hi))
        for bits in (8, 16, 32):
            if fits(lo, hi, bits):
                verdict = f"int{bits}"
                break
        else:
            verdict = ">int32"
        w(f"  {name:6s} {lo:12d} {hi:12d} {peak:12d}  {verdict:>6s}\n")

    if scaled:
        return

    red = reduced(baked)
    minx = min(v[0] for v in vertexes)
    miny = min(v[1] for v in vertexes)

    w("\nconsistent scaling: gcd-reduced normals, coords translated to origin and >>k\n")
    w(f"  {'k':>2} {'unit':>6} {'coord':>7} {'|n|':>5} {'|D|':>7} {'|acc|':>7}  {'distinct':>9}  fits\n")
    for k in range(3, 9):
        pts = [((x - minx) >> k, (y - miny) >> k) for x, y in vertexes]
        cmax = max(max(abs(a), abs(b)) for a, b in pts)
        npeak = dpeak = apeak = 0
        for (nx, ny), (v1, _v2, _f, _t) in zip(red, linedefs):
            px, py = pts[v1]
            npeak = max(npeak, abs(nx), abs(ny))
            dpeak = max(dpeak, abs(nx * px + ny * py))
            apeak = max(apeak, abs(nx) * cmax + abs(ny) * cmax)
        ok = "int8" if (cmax <= 127 and npeak <= 127 and apeak <= 32767) else "--"
        w(f"  {k:>2} {1 << k:>6} {cmax:>7} {npeak:>5} {dpeak:>7} {apeak:>7}  "
          f"{len(set(pts)):>4}/{len(pts):<4}  {ok}\n")
    w("\n  coord/|n| must fit int8; |acc| is the worst-case MAC sum, must fit int16\n\n")


def c_ident(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name).lower()


def emit_c(out, wadpath, mapname, vertexes, linedefs, baked, columns, scale=None):
    ident = c_ident(mapname)
    guard = f"{ident.upper()}_MAP_H"
    xs = [v[0] for v in vertexes]
    ys = [v[1] for v in vertexes]

    w = out.write
    w(f"/* Generated by wad2c.py from {Path(wadpath).name} -- do not edit. */\n")
    w(f"#ifndef {guard}\n#define {guard}\n\n#include <stdint.h>\n\n")

    v_t = c_int_type(min(min(xs), min(ys)), max(max(xs), max(ys)))
    if baked:
        n_t = c_int_type(min(min(b[0], b[1]) for b in baked),
                         max(max(b[0], b[1]) for b in baked))
        d_t = c_int_type(min(b[2] for b in baked), max(b[2] for b in baked))
        types_guard = (f"DOOM_MAP_TYPES_{v_t[:-2].upper()}_"
                       f"{n_t[:-2].upper()}_{d_t[:-2].upper()}_H")
    else:
        types_guard = f"DOOM_MAP_TYPES_{v_t[:-2].upper()}_H"

    w(f"#ifndef {types_guard}\n#define {types_guard}\n")
    w(f"typedef struct {{ {v_t} x, y; }} map_vertex_t;\n")
    if baked:
        w("typedef struct { uint16_t v1, v2; uint16_t flags;\n")
        w(f"                 {n_t} nx, ny; {d_t} d; }} map_linedef_t;\n")
    else:
        w("typedef struct { uint16_t v1, v2; uint16_t flags; } map_linedef_t;\n")
    w("#define ML_BLOCKING      0x0001\n")
    w("#define ML_BLOCKMONSTERS 0x0002\n")
    w("#define ML_TWOSIDED      0x0004\n")
    w("#define ML_DONTPEGTOP    0x0008\n")
    w("#define ML_DONTPEGBOTTOM 0x0010\n")
    w("#define ML_SECRET        0x0020\n")
    w("#define ML_SOUNDBLOCK    0x0040\n")
    w("#define ML_DONTDRAW      0x0080\n")
    w("#define ML_MAPPED        0x0100\n")
    w("#define ML_GEN_TWOSIDED  0x8000\n")
    w("#endif\n\n")

    w(f"#define {ident.upper()}_NUM_VERTEXES {len(vertexes)}\n")
    w(f"#define {ident.upper()}_NUM_LINEDEFS {len(linedefs)}\n")
    w(f"#define {ident.upper()}_MIN_X ({min(xs)})\n")
    w(f"#define {ident.upper()}_MAX_X ({max(xs)})\n")
    w(f"#define {ident.upper()}_MIN_Y ({min(ys)})\n")
    w(f"#define {ident.upper()}_MAX_Y ({max(ys)})\n")
    w(f"#define {ident.upper()}_WIDTH  ({max(xs) - min(xs)})\n")
    w(f"#define {ident.upper()}_HEIGHT ({max(ys) - min(ys)})\n")
    if scale is not None:
        k, ox, oy = scale
        w(f"#define {ident.upper()}_SCALE_SHIFT ({k})\n")
        w(f"#define {ident.upper()}_ORIGIN_X ({ox})\n")
        w(f"#define {ident.upper()}_ORIGIN_Y ({oy})\n")
    if baked:
        w(f"#define {ident.upper()}_MAX_ABS_N ({max(max(abs(b[0]), abs(b[1])) for b in baked)})\n")
        w(f"#define {ident.upper()}_MAX_ABS_D ({max(abs(b[2]) for b in baked)})\n")
    w("\n")

    w(f"static const map_vertex_t {ident}_vertexes[{len(vertexes)}] = {{\n")
    for i in range(0, len(vertexes), columns):
        row = ", ".join(f"{{{x:6d},{y:6d}}}" for x, y in vertexes[i:i + columns])
        w(f"    {row},\n")
    w("};\n\n")

    w(f"static const map_linedef_t {ident}_linedefs[{len(linedefs)}] = {{\n")
    for i, (v1, v2, flags, two_sided) in enumerate(linedefs):
        out_flags = flags | (0x8000 if two_sided else 0)
        if baked:
            nx, ny, d = baked[i]
            w(f"    {{{v1:4d},{v2:4d}, 0x{out_flags:04X}, {nx:6d},{ny:6d}, {d:11d}}},\n")
        else:
            w(f"    {{{v1:4d},{v2:4d}, 0x{out_flags:04X}}},\n")
    w("};\n\n")

    w("#endif\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wad")
    ap.add_argument("--map", default="E1M1", help="map marker name (default: E1M1)")
    ap.add_argument("-o", "--output", help="output .h file (default: stdout)")
    ap.add_argument("--skip-twosided", action="store_true",
                    help="emit only one-sided lines (outer walls)")
    ap.add_argument("--prebake", action="store_true",
                    help="precompute per-line normal nx,ny and plane constant D")
    ap.add_argument("--reduce-normals", action="store_true",
                    help="divide each baked normal by gcd(|nx|,|ny|); implies --prebake")
    ap.add_argument("--scale-coords", type=int, metavar="K",
                    help="translate vertexes to the map origin and shift them right by K, "
                         "baking D in the same units; implies --prebake")
    ap.add_argument("--columns", type=int, default=4,
                    help="vertexes per source line (default: 4)")
    ap.add_argument("--subsectors", action="store_true",
                    help="emit SSECTORS/SEGS with per-subsector local origins "
                         "(bbox centre) and locally-baked D; uses --scale-coords (default 3)")
    ap.add_argument("--heights", action="store_true",
                    help="also emit per-seg front/back sector floor and ceiling "
                         "heights (needs SIDEDEFS and SECTORS)")
    ap.add_argument("--nodes", action="store_true",
                    help="also emit the BSP NODES array (partition line + children); "
                         "implies --subsectors")
    ap.add_argument("--list-maps", action="store_true",
                    help="list the map markers in the WAD and exit")
    args = ap.parse_args(argv)

    try:
        data = Path(args.wad).read_bytes()
        _wadtype, directory = read_directory(data)

        if args.list_maps:
            names = [n for i, (n, _, s) in enumerate(directory)
                     if s == 0 and i + 1 < len(directory) and directory[i + 1][0] in MAP_LUMPS]
            print(" ".join(names))
            return 0

        lumps = find_map_lumps(directory, args.map)
        for need in ("VERTEXES", "LINEDEFS"):
            if need not in lumps:
                raise WadError(f"{args.map} has no {need} lump")

        vertexes = parse_vertexes(data, *lumps["VERTEXES"])
        linedefs = parse_linedefs(data, *lumps["LINEDEFS"], nvertexes=len(vertexes))

        if args.subsectors or args.nodes or args.heights:
            for need in ("SEGS", "SSECTORS"):
                if need not in lumps:
                    raise WadError(f"{args.map} has no {need} lump")
            k = 3 if args.scale_coords is None else args.scale_coords
            if not 0 <= k < 16:
                raise WadError(f"--scale-coords must be in 0..15, got {k}")
            segs = parse_segs(data, *lumps["SEGS"], nvertexes=len(vertexes))
            for si, s in enumerate(segs):
                if s[2] >= len(linedefs):
                    raise WadError(f"seg {si} references linedef {s[2]} "
                                   f"but the map only has {len(linedefs)}")
            ssectors = parse_ssectors(data, *lumps["SSECTORS"], nsegs=len(segs))
            normals = linedef_normals(vertexes, linedefs)
            lineflags, dropped = linedef_flag_bytes(linedefs)
            if dropped:
                print(f"wad2c.py: note: {dropped} linedefs have flag bits above 0x00FF; "
                      "those bits are not in the seg flag byte", file=sys.stderr)
            subs, lsegs = build_subsectors(vertexes, segs, ssectors,
                                           normals, lineflags, k)
            check_widths(subs, lsegs)
            segheights = None
            if args.heights:
                for need in ("SIDEDEFS", "SECTORS"):
                    if need not in lumps:
                        raise WadError(f"{args.map} has no {need} lump")
                sectors = parse_sectors(data, *lumps["SECTORS"])
                sidedefs = parse_sidedefs(data, *lumps["SIDEDEFS"], nsectors=len(sectors))
                linesides = parse_linedef_sides(data, *lumps["LINEDEFS"])
                segheights = build_seg_heights(segs, linesides, sidedefs, sectors)

            bspnodes = None
            if args.nodes:
                if "NODES" not in lumps:
                    raise WadError(f"{args.map} has no NODES lump")
                bspnodes = parse_nodes(data, *lumps["NODES"], nsubs=len(subs))
    except (OSError, WadError) as e:
        print(f"wad2c.py: {e}", file=sys.stderr)
        return 1

    if args.subsectors or args.nodes or args.heights:
        if args.output:
            with open(args.output, "w") as f:
                emit_subsectors_c(f, args.wad, args.map, subs, lsegs, k, args.columns,
                                  bspnodes, segheights)
            dest = args.output
        else:
            emit_subsectors_c(sys.stdout, args.wad, args.map, subs, lsegs, k,
                              args.columns, bspnodes, segheights)
            dest = "stdout"
        extra = f", {len(bspnodes)} nodes" if bspnodes else ""
        print(f"{args.map}: {len(subs)} subsectors, {len(lsegs)} segs{extra} -> {dest}",
              file=sys.stderr)
        report_subsectors(subs, lsegs, sys.stderr)
        return 0

    kept = [l for l in linedefs if not (args.skip_twosided and l[3])]

    scale = None
    geom = None
    if args.scale_coords is not None:
        k = args.scale_coords
        if not 0 <= k < 16:
            print(f"wad2c.py: --scale-coords must be in 0..15, got {k}", file=sys.stderr)
            return 1
        ox = min(v[0] for v in vertexes)
        oy = min(v[1] for v in vertexes)
        geom = vertexes
        vertexes = [((x - ox) >> k, (y - oy) >> k) for x, y in vertexes]
        scale = (k, ox, oy)

    baked = (bake(vertexes, kept, args.reduce_normals, geom)
             if (args.prebake or args.reduce_normals or scale) else None)

    if args.output:
        with open(args.output, "w") as f:
            emit_c(f, args.wad, args.map, vertexes, kept, baked, args.columns, scale)
        dest = args.output
    else:
        emit_c(sys.stdout, args.wad, args.map, vertexes, kept, baked, args.columns, scale)
        dest = "stdout"

    print(f"{args.map}: {len(vertexes)} vertexes, {len(kept)}/{len(linedefs)} linedefs -> {dest}",
          file=sys.stderr)
    if baked:
        report_ranges(baked, vertexes, kept, sys.stderr, scaled=scale is not None)
        if scale is not None:
            report_residuals(baked, vertexes, kept, sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())