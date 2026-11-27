"""Tidy the frozen routing in hw/routes.py: straighten jogs and needless bends, one trace run at a time.

A run is the stretch of one net on one layer between fixed points: pads, vias, branches and width
changes. For each run, a small search picks the cheapest path through the run's own corners, where
each hop is either the original track or a new octilinear path (straight, or two segments at 45 and
0/90 degrees). Cost is length plus a penalty per bend, so a jog that only adds corners goes away even
when it saves no length. Every new segment is checked against the copper of other nets (tracks,
arcs, vias, pads, holes), the rule areas that ban tracks, the fixed-net pours and the board edge.
KiCad's DRC checks the result afterwards (hw/route.py, then kicad-cli).

Run: python3 hw/tools/tidy_routes.py [--dry-run] [--skip NET,...]
Needs hw/out/tidy_obstacles.json from `kicad-python hw/tools/tidy_extract.py` on the routed board.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hardware.pcb_datum import point_in_pcb  # noqa: E402

CLR = 0.11            # 0.10 mm class clearance plus a margin
CLR_ANALOG = 0.15     # between an analog net and any other class
CLR_HV = 0.42         # the 0.40 mm HV divider rule
HV_NETS = {"CH1_TIP", "CH1_HV_MID", "CH2_TIP", "CH2_HV_MID"}
VIA_PAD, VIA_HOLE, HOLE_CLR = 0.225, 0.125, 0.25
EDGE_CLR = 0.31
BEND = 0.45           # mm of track a bend is worth
MIN_GAIN = 0.05
CORE_NETS = {"+3V3_DIG", "+1V1_CORE", "GND"}
FIXED_ZONES = ("regulator ", "B +3V3_DIG under MCU")
EPS = 1e-4
CELL = 1.0
DEBUG = __import__("os").environ.get("TIDY_DEBUG")

# ---------------------------------------------------------------- geometry


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def pt_seg(p, a, b):
    ab = sub(b, a)
    L2 = dot(ab, ab)
    if L2 < 1e-12:
        return dist(p, a)
    t = max(0.0, min(1.0, dot(sub(p, a), ab) / L2))
    return dist(p, (a[0] + ab[0] * t, a[1] + ab[1] * t))


def seg_cross(a, b, c, d):
    d1 = cross(sub(b, a), sub(c, a))
    d2 = cross(sub(b, a), sub(d, a))
    d3 = cross(sub(d, c), sub(a, c))
    d4 = cross(sub(d, c), sub(b, c))
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def seg_seg(a, b, c, d):
    if seg_cross(a, b, c, d):
        return 0.0
    return min(pt_seg(a, c, d), pt_seg(b, c, d), pt_seg(c, a, b), pt_seg(d, a, b))


def in_poly(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


def seg_poly(a, b, poly):
    if in_poly(a, poly) or in_poly(b, poly):
        return 0.0
    n = len(poly)
    return min(seg_seg(a, b, poly[i], poly[(i + 1) % n]) for i in range(n))


def arc_pts(p1, pm, p2, steps=8):
    """Polyline through an arc given by start, mid and end."""
    ax, ay = p1
    bx, by = pm
    cx, cy = p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return [p1, p2]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.atan2(ay - uy, ax - ux)
    am = math.atan2(by - uy, bx - ux)
    a1 = math.atan2(cy - uy, cx - ux)

    def norm(t):
        while t < a0:
            t += 2 * math.pi
        while t >= a0 + 2 * math.pi:
            t -= 2 * math.pi
        return t
    am_n, a1_n = norm(am), norm(a1)
    if am_n > a1_n:        # clockwise
        a1_n -= 2 * math.pi
    return [(ux + r * math.cos(a0 + (a1_n - a0) * k / steps), uy + r * math.sin(a0 + (a1_n - a0) * k / steps))
            for k in range(steps + 1)]


def octi(d):
    """Index 0..7 of an octilinear direction, or None."""
    dx, dy = d
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return None
    if abs(dy) < 1e-3:
        return 0 if dx > 0 else 4
    if abs(dx) < 1e-3:
        return 2 if dy > 0 else 6
    if abs(abs(dx) - abs(dy)) < 2e-3:
        if dx > 0:
            return 1 if dy > 0 else 7
        return 3 if dy > 0 else 5
    return None


def turn(d0, d1):
    """Number of 45-degree steps between two direction indices (0..4)."""
    t = abs(d0 - d1) % 8
    return min(t, 8 - t)


# ---------------------------------------------------------------- obstacles


class Layer:
    def __init__(self):
        self.items = {}          # id -> (net, kind, geom, clr_override)
        self.grid = defaultdict(set)
        self.cells = {}

    def _bbox_cells(self, bb):
        x0, y0, x1, y1 = bb
        for i in range(int(math.floor(x0 / CELL)), int(math.floor(x1 / CELL)) + 1):
            for j in range(int(math.floor(y0 / CELL)), int(math.floor(y1 / CELL)) + 1):
                yield (i, j)

    def add(self, oid, net, kind, geom, bb, extra=None):
        self.items[oid] = (net, kind, geom, extra)
        cells = list(self._bbox_cells(bb))
        self.cells[oid] = cells
        for c in cells:
            self.grid[c].add(oid)

    def remove(self, oid):
        for c in self.cells.pop(oid):
            self.grid[c].discard(oid)
        del self.items[oid]

    def near(self, bb):
        out = set()
        for c in self._bbox_cells(bb):
            out |= self.grid.get(c, set())
        return out


def bbox(pts, pad=0.0):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


class Board:
    def __init__(self, routes, obst):
        self.netclass = obst["netclass"]
        self.layers = {n: Layer() for n in ("F", "In1", "In2", "B")}
        self.uid = 0
        self.tracks = {}       # tid -> [net, layer, w, kind, pts]  (pts: 2 for seg, 3 for arc)
        for n, lay, w, x1, y1, x2, y2 in routes.SEGMENTS:
            self.add_track(n, lay, w, "seg", [(x1, y1), (x2, y2)])
        for n, lay, w, x1, y1, xm, ym, x2, y2 in routes.ARCS:
            self.add_track(n, lay, w, "arc", [(x1, y1), (xm, ym), (x2, y2)])
        self.vias = [tuple(v) for v in routes.VIAS]
        for n, x, y, size, drill in self.vias:
            r = max(size / 2 + CLR, drill / 2 + HOLE_CLR)
            for L in self.layers.values():
                self._add_static(L, n, "circle", ((x, y), r, True), bbox([(x, y)], r))
        self.pads = defaultdict(list)     # (net, layer) -> polys
        for p in obst["pads"]:
            poly = [tuple(q) for q in p["poly"]]
            self.pads[(p["net"], p["layer"])].append(poly)
            self._add_static(self.layers[p["layer"]], p["net"], "poly", poly, bbox(poly))
        for h in obst["holes"]:
            r = h["r"] + HOLE_CLR
            for L in self.layers.values():
                self._add_static(L, h["net"] if not h["npth"] else "", "circle", (tuple(h["xy"]), r, True),
                                 bbox([h["xy"]], r))
        for a in obst["areas"]:
            if a["name"] == "mcu_core_b":
                kind = "core"
            elif a["no_tracks"]:
                kind = "keepout"
            else:
                continue
            for lay in a["layers"]:
                for poly in a["polys"]:
                    poly = [tuple(q) for q in poly]
                    self._add_static(self.layers[lay], "*", kind, poly, bbox(poly))
        for z in obst["zones"]:
            if not z["name"].startswith(FIXED_ZONES):
                continue
            for lay in z["layers"]:
                for poly in z["polys"]:
                    poly = [tuple(q) for q in poly]
                    self._add_static(self.layers[lay], z["net"], "poly", poly, bbox(poly))

    def _add_static(self, L, net, kind, geom, bb):
        self.uid += 1
        L.add(("s", self.uid), net, kind, geom, bb)

    def add_track(self, net, lay, w, kind, pts):
        self.uid += 1
        tid = ("t", self.uid)
        self.tracks[tid] = [net, lay, w, kind, pts]
        poly = arc_pts(*pts) if kind == "arc" else pts
        self.layers[lay].add(tid, net, "track", (poly, w / 2), bbox(poly, w / 2))
        return tid

    def remove_track(self, tid):
        net, lay, *_ = self.tracks.pop(tid)
        self.layers[lay].remove(tid)

    def clr(self, a, b):
        if a in HV_NETS or b in HV_NETS:
            return CLR_HV
        ca, cb = self.netclass.get(a), self.netclass.get(b)
        if (ca == "ANALOG") != (cb == "ANALOG"):
            return CLR_ANALOG
        return CLR

    def clear(self, net, lay, a, b, w):
        """True when a new track a-b of width w on lay keeps clear of everything not on its net."""
        hw = w / 2
        # board edge, sampled
        n = max(2, int(dist(a, b) / 0.1) + 1)
        for k in range(n + 1):
            t = k / n
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if not point_in_pcb(p[0], p[1], margin=EDGE_CLR + hw):
                return False
        L = self.layers[lay]
        for oid in L.near(bbox([a, b], hw + 0.5)):
            onet, kind, geom, _ = L.items[oid]
            if kind == "core":
                if net not in CORE_NETS and seg_poly(a, b, geom) < hw:
                    return False
                continue
            if kind == "keepout":
                if seg_poly(a, b, geom) < hw:
                    return False
                continue
            if onet == net:
                continue
            if kind == "track":
                poly, ohw = geom
                need = hw + ohw + self.clr(net, onet)
                for i in range(len(poly) - 1):
                    if seg_seg(a, b, poly[i], poly[i + 1]) < need:
                        return False
            elif kind == "circle":
                c, r, absolute = geom
                if pt_seg(c, a, b) < hw + r:
                    return False
            elif kind == "poly":
                if seg_poly(a, b, geom) < hw + self.clr(net, onet):
                    return False
        return True

    def in_own_pad(self, net, lay, p):
        return any(in_poly(p, poly) for poly in self.pads.get((net, lay), ()))


# ---------------------------------------------------------------- runs


def key(p):
    return (round(p[0], 3), round(p[1], 3))


def runs(bd, net, lay):
    """Split the net's tracks on one layer into runs between fixed points."""
    tids = [t for t, v in bd.tracks.items() if v[0] == net and v[1] == lay]
    ends = defaultdict(list)
    for t in tids:
        pts = bd.tracks[t][4]
        ends[key(pts[0])].append(t)
        ends[key(pts[-1])].append(t)
    vias = [((x, y), size / 2) for n, x, y, size, _ in bd.vias if n == net]
    pads = bd.pads.get((net, lay), ())
    frozen = set()
    tee = set()

    def poly_of(u):
        up = bd.tracks[u][4]
        return arc_pts(*up) if bd.tracks[u][3] == "arc" else up

    all_ends = [bd.tracks[t][4][k] for t in tids for k in (0, -1)]
    for u in tids:
        # a track whose body (not its ends) touches a same-net via or pad that no track ends on carries that
        # connection: keep it
        poly, hw = poly_of(u), bd.tracks[u][2] / 2
        body = [(poly[i], poly[i + 1]) for i in range(len(poly) - 1)]
        e0, e1 = poly[0], poly[-1]
        for c, r in vias:
            if any(dist(c, e) < r for e in all_ends):
                continue
            if any(pt_seg(c, a, b) < r + hw for a, b in body):
                frozen.add(u)
        for pp in pads:
            if any(in_poly(e, pp) for e in all_ends):
                continue
            if any(seg_poly(a, b, pp) < hw for a, b in body):
                frozen.add(u)
    for t in tids:
        _, _, w, kind, pts = bd.tracks[t]
        for k in (key(pts[0]), key(pts[-1])):
            if len(ends[k]) != 1:
                continue        # a corner: joined end to end already
            for u in tids:
                if u == t:
                    continue
                up = bd.tracks[u][4]
                if k in (key(up[0]), key(up[-1])):
                    continue
                poly = poly_of(u)
                # an end inside (or touching) another track's copper is a connection
                if any(pt_seg(k, poly[i], poly[i + 1]) < (bd.tracks[u][2] + w) / 2 for i in range(len(poly) - 1)):
                    frozen.add(u)
                    tee.add(k)

    def anchor(k):
        ts = ends[k]
        if len(ts) != 2 or k in tee:
            return True
        w = max(bd.tracks[t][2] for t in ts)
        if any(dist(k, c) < r + w / 2 for c, r in vias):
            return True
        if any(seg_poly(k, k, pp) < w / 2 + 0.01 for pp in pads):
            return True
        if abs(bd.tracks[ts[0]][2] - bd.tracks[ts[1]][2]) > 1e-6:
            return True
        return False

    if DEBUG == net:
        print("frozen", [bd.tracks[u][4] for u in frozen], "tee", tee)
        print("anchors", [k for k in ends if anchor(k)])
    seen = set()
    out = []
    for start in [k for k in ends if anchor(k)]:
        for t0 in ends[start]:
            if t0 in seen:
                continue
            chain, verts = [], [start]
            t, cur = t0, start
            while True:
                seen.add(t)
                pts = bd.tracks[t][4]
                nxt = key(pts[-1]) if key(pts[0]) == cur else key(pts[0])
                chain.append((t, cur == key(pts[0])))
                verts.append(nxt)
                if anchor(nxt):
                    break
                t = [u for u in ends[nxt] if u != t][0]
                if t in seen:
                    break
                cur = nxt
            if not any(t in frozen for t, _ in chain):
                out.append((chain, verts))
    return out


def seg_len(bd, t):
    _, _, _, kind, pts = bd.tracks[t]
    poly = arc_pts(*pts) if kind == "arc" else pts
    return sum(dist(poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def paths(a, b):
    """Octilinear ways from a to b: straight, or two segments (45 degrees plus 0/90), both orders."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if octi((dx, dy)) is not None:
        return [[a, b]]
    sx, sy = math.copysign(1, dx), math.copysign(1, dy)
    if abs(dx) >= abs(dy):
        c1 = (a[0] + sx * abs(dy), b[1])            # diagonal first
        c2 = (b[0] - sx * abs(dy), a[1])            # straight first
    else:
        c1 = (b[0], a[1] + sy * abs(dx))
        c2 = (a[0], b[1] - sy * abs(dx))
    return [[a, c1, b], [a, c2, b]]


def tidy_run(bd, net, lay, chain, verts):
    w = bd.tracks[chain[0][0]][2]
    n = len(chain)
    P = [verts[0]] + [None] * n
    # exact vertex coordinates from the tracks
    for i, (t, fwd) in enumerate(chain):
        pts = bd.tracks[t][4]
        P[i] = pts[0] if fwd else pts[-1]
        P[i + 1] = pts[-1] if fwd else pts[0]

    # runs that thread through their own pads (pad-to-pad lattices, as in the nose terminal) stay as drawn
    for pp in bd.pads.get((net, lay), ()):
        if any(in_poly(P[k], pp) for k in range(1, n)) or (in_poly(P[0], pp) and in_poly(P[n], pp)):
            return None

    def odir(t, fwd):
        _, _, _, kind, pts = bd.tracks[t]
        if kind == "arc":
            return None
        a, b = (pts[0], pts[1]) if fwd else (pts[1], pts[0])
        return octi(sub(b, a))

    def bend_cost(din, dnew):
        if din is None or dnew is None:
            return 0.0, True
        t = turn(din, dnew)
        if t > 2:
            return 0.0, False
        return (BEND if t == 1 else 1.5 * BEND) if t else 0.0, True

    INF = float("inf")
    best = [dict() for _ in range(n + 1)]     # vertex -> {dir_in: (cost, back)}
    best[0][None] = (0.0, None)
    orig_best = [dict() for _ in range(n + 1)]
    orig_best[0][None] = (0.0, None)
    for i in range(n):
        # original track i only, for the reference cost
        t, fwd = chain[i]
        d = odir(t, fwd)
        for din, (c, _) in orig_best[i].items():
            bc, ok = bend_cost(din, d)
            cc = c + seg_len(bd, t) + bc
            dout = d if bd.tracks[t][3] == "seg" else None
            if cc < orig_best[i + 1].get(dout, (INF,))[0]:
                orig_best[i + 1][dout] = (cc, None)
    for i in range(n):
        if not best[i]:
            continue
        for din, (c, _) in list(best[i].items()):
            t, fwd = chain[i]
            d = odir(t, fwd)
            bc, ok = bend_cost(din, d)
            if ok:
                cc = c + seg_len(bd, t) + bc
                dout = d if bd.tracks[t][3] == "seg" else None
                if cc < best[i + 1].get(dout, (INF,))[0]:
                    best[i + 1][dout] = (cc, (i, din, ("orig", i)))
            for j in range(i + 1, n + 1):
                for path in paths(P[i], P[j]):
                    if j == i + 1 and len(path) == 2 and bd.tracks[t][3] == "seg":
                        continue
                    dirs = [octi(sub(path[k + 1], path[k])) for k in range(len(path) - 1)]
                    if any(x is None for x in dirs):
                        continue
                    bc, ok = bend_cost(din, dirs[0])
                    if not ok:
                        continue
                    for k in range(1, len(dirs)):
                        b2, ok2 = bend_cost(dirs[k - 1], dirs[k])
                        if not ok2:
                            ok = False
                            break
                        bc += b2
                    if not ok:
                        continue
                    L = sum(dist(path[k], path[k + 1]) for k in range(len(path) - 1))
                    cc = c + L + bc
                    if cc >= best[j].get(dirs[-1], (INF,))[0]:
                        continue
                    if not all(bd.clear(net, lay, path[k], path[k + 1], w) for k in range(len(path) - 1)):
                        continue
                    best[j][dirs[-1]] = (cc, (i, din, ("new", path)))
    # A run that ends inside a pad of its own net may end anywhere well inside that pad: try a straight
    # octilinear entry from each corner, ending where it passes closest to the original end.
    E = P[n]
    own = [pp for pp in bd.pads.get((net, lay), ()) if in_poly(E, pp)]
    if own:
        pad = own[0]

        def deep_inside(q):
            if not in_poly(q, pad):
                return False
            m = len(pad)
            return min(pt_seg(q, pad[k], pad[(k + 1) % m]) for k in range(m)) >= w / 2 + 0.02

        for i in range(n):
            for din, (c, _) in list(best[i].items()):
                for dd in range(8):
                    ang = dd * math.pi / 4
                    u = (math.cos(ang), math.sin(ang))
                    tt = dot(sub(E, P[i]), u)
                    if tt <= 0.05:
                        continue
                    q = (round(P[i][0] + u[0] * tt, 4), round(P[i][1] + u[1] * tt, 4))
                    if not deep_inside(q):
                        continue
                    bc, ok = bend_cost(din, dd)
                    if not ok:
                        continue
                    cc = c + tt + bc - 0.01      # break ties toward the straight entry
                    if cc >= best[n].get(dd, (INF,))[0]:
                        continue
                    if not bd.clear(net, lay, P[i], q, w):
                        continue
                    best[n][dd] = (cc, (i, din, ("new", [P[i], q])))
    if not best[n]:
        return None
    dend, (cnew, _) = min(best[n].items(), key=lambda kv: kv[1][0])
    corig = min(v[0] for v in orig_best[n].values())
    if cnew > corig - MIN_GAIN:
        return None
    # walk back
    steps = []
    j, d = n, dend
    while j > 0:
        c, back = best[j][d]
        i, din, step = back
        steps.append(step)
        j, d = i, din
    steps.reverse()
    if all(s[0] == "orig" for s in steps):
        return None
    return steps, corig, cnew


def apply(bd, net, lay, chain, steps):
    w = bd.tracks[chain[0][0]][2]
    keep = {chain[s[1]][0] for s in steps if s[0] == "orig"}
    for t, _ in chain:
        if t not in keep:
            bd.remove_track(t)
    # new straight pieces, merged where consecutive pieces run the same way
    pieces = []
    for s in steps:
        if s[0] == "new":
            path = s[1]
            for k in range(len(path) - 1):
                pieces.append([path[k], path[k + 1]])
        else:
            pieces.append(None)
    merged = []
    for pc in pieces:
        if pc and merged and merged[-1] and octi(sub(merged[-1][1], merged[-1][0])) == octi(sub(pc[1], pc[0])):
            merged[-1][1] = pc[1]
        else:
            merged.append(pc)
    added = []
    for pc in merged:
        if pc:
            a = (round(pc[0][0], 4), round(pc[0][1], 4))
            b = (round(pc[1][0], 4), round(pc[1][1], 4))
            if dist(a, b) > 1e-4:
                added.append(bd.add_track(net, lay, w, "seg", [a, b]))
    return added


def main():
    dry = "--dry-run" in sys.argv
    skip = set()
    if "--skip" in sys.argv:
        skip = set(sys.argv[sys.argv.index("--skip") + 1].split(","))
    spec = importlib.util.spec_from_file_location("routes", ROOT / "hw/routes.py")
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    obst = json.loads((ROOT / "hw/out/tidy_obstacles.json").read_text())
    bd = Board(routes, obst)
    nets = sorted({(v[0], v[1]) for v in bd.tracks.values()})
    log = []
    for rnd in range(6):
        changed = 0
        for net, lay in nets:
            if net in skip:
                continue
            for chain, verts in runs(bd, net, lay):
                if rnd % 2:        # every other round walks the runs backwards, so their starts get the pad slide
                    chain = [(t, not f) for t, f in reversed(chain)]
                    verts = list(reversed(verts))
                if any(bd.tracks[t][2] >= 0.5 for t, _ in chain):
                    continue        # In2 supply rails
                r = tidy_run(bd, net, lay, chain, verts)
                if not r:
                    continue
                steps, c0, c1 = r
                old = [list(bd.tracks[t]) for t, _ in chain]
                added = apply(bd, net, lay, chain, steps)
                log.append({"net": net, "layer": lay, "gain": round(c0 - c1, 3),
                            "old": [[o[3], o[4]] for o in old],
                            "new": [bd.tracks[t][4] for t in added]})
                changed += 1
        print(f"round {rnd}: {changed} runs tidied")
        if not changed:
            break
    (ROOT / "hw/out/tidy_log.json").write_text(json.dumps(log, indent=0))
    total = sum(e["gain"] for e in log)
    print(f"{len(log)} runs changed, cost saved {total:.1f} (length + bends)")
    if dry:
        return
    src = (ROOT / "hw/routes.py").read_text()
    header = src[:src.index("SEGMENTS = [")]
    segs = sorted(tuple(v[:3]) + (*v[4][0], *v[4][1]) for v in bd.tracks.values() if v[3] == "seg")
    arcs = sorted(tuple(v[:3]) + (*v[4][0], *v[4][1], *v[4][2]) for v in bd.tracks.values() if v[3] == "arc")
    out = [header + "SEGMENTS = ["] + [f"    {t!r}," for t in segs] + ["]", "", "ARCS = ["]
    out += [f"    {t!r}," for t in arcs] + ["]", "", "VIAS = ["] + [f"    {t!r}," for t in bd.vias] + ["]", ""]
    (ROOT / "hw/routes.py").write_text("\n".join(out))
    print("wrote hw/routes.py")


if __name__ == "__main__":
    main()
