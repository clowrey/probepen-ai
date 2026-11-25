"""Deterministic 4-layer octilinear PCB router and zone filler for ProbePen-AI.

Run with KiCad's Python after hw/gen_pcb.py:
    kicad-python hw/route.py

Design principles:
  - Signals & non-plane power traces run exclusively on F.Cu and B.Cu with 0/45/90-degree
    octilinear legs and 0.45 mm / 0.20 mm vias (leaving In1.Cu GND/AGND and In2.Cu +3V3_DIG/+3V3_ANA
    unbroken by signal traces).
  - Step 0 pre-commits grid-snapped radial breakout stubs on all fine-pitch SMD pads BEFORE any
    vias or traces are placed, eliminating both stub-on-track shorts and 0.02 mm grid-snap gaps.
  - Analytical spatial-bucket clearance checking enforces KiCad DRC clearances (0.15 mm LV/POWER/
    USB/HS, 0.13 mm ANALOG, 0.40 mm HV /PROBE_TIP & /HV_MID, 0.25 mm holes, 0.30 mm board edge).
  - Plane-backed nets drop local vias directly into In1.Cu / In2.Cu; pads outside a zone polygon
    route to the nearest plane via; ZONE_FILLER fills all copper pours and GND stitching vias tie
    top/bottom return paths cleanly.
"""
from __future__ import annotations

import heapq
import math
import sys
from collections import defaultdict
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from hw.gen_pcb import (  # noqa: E402
    AFE_X0, AFE_X1, AFE_Y0, AFE_Y1, HW_Y, L, MM, NET, OX, OY, PCB_FILE, R_NOSE, R_TAIL,
)
from hw import design, routes as route_cfg  # noqa: E402

RES = 0.10  # 100 um routing grid (exact match to 0.4/0.5 mm fine-pitch pin centers & 4x faster search)
NX = int(round(L / RES)) + 1           # 1321
NY = int(round((2 * HW_Y) / RES)) + 1  # 151
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu, pcbnew.In2_Cu)
VIA_R = route_cfg.VIA_DIAM_MM / 2.0

DIRS = [
    (1, 0, 1000), (-1, 0, 1000), (0, 1, 1000), (0, -1, 1000),
    (1, 1, 1414), (1, -1, 1414), (-1, 1, 1414), (-1, -1, 1414),
]


def gx(x: float) -> int:
    return max(0, min(NX - 1, int(round(x / RES))))


def gy(y: float) -> int:
    return max(0, min(NY - 1, int(round((y + HW_Y) / RES))))


def wx(ix: int) -> float:
    return round(ix * RES, 4)


def wy(iy: int) -> float:
    return round(iy * RES - HW_Y, 4)


def snap(x: float, y: float) -> tuple[float, float]:
    return wx(gx(x)), wy(gy(y))


def point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def inside_board(x: float, y: float, margin: float) -> bool:
    if x < margin or x > L - margin or y < -HW_Y + margin or y > HW_Y - margin:
        return False
    if x < R_NOSE and abs(y) > HW_Y - R_NOSE:
        if math.hypot(R_NOSE - x, abs(y) - (HW_Y - R_NOSE)) > R_NOSE - margin:
            return False
    if x > L - R_TAIL and abs(y) > HW_Y - R_TAIL:
        if math.hypot(x - (L - R_TAIL), abs(y) - (HW_Y - R_TAIL)) > R_TAIL - margin:
            return False
    return True


def in_afe_keepout(x: float, y: float, margin: float = 0.35) -> bool:
    return (AFE_X0 - margin) <= x <= (AFE_X1 + margin) and (AFE_Y0 - margin) <= y <= (AFE_Y1 + margin)


def in_u1_collar(x: float, y: float) -> bool:
    """Keep 0.45 mm vias out of U1's 0.40 mm-pitch breakout collar (allow vias inside U1 or outer ring)."""
    ux, uy = 109.2, 0.0
    in_outer = (ux - 6.35) <= x <= (ux + 6.35) and (uy - 6.15) <= y <= (uy + 6.15)
    in_inner_clear = (ux - 4.30) <= x <= (ux + 4.30) and (uy - 4.30) <= y <= (uy + 4.30)
    return in_outer and not in_inner_clear


def _rect_box_dist(x: float, y: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx = 0.0 if x0 <= x <= x1 else (x0 - x if x < x0 else x - x1)
    dy = 0.0 if y0 <= y <= y1 else (y0 - y if y < y0 else y - y1)
    return math.hypot(dx, dy)


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    d2 = vx * vx + vy * vy
    if d2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / d2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


class ObstacleMap:
    """Spatial index + exact geometric clearance verifier for F.Cu, B.Cu, In2.Cu, drills, and vias."""

    def __init__(self, board: pcbnew.BOARD):
        self.board = board
        self.buckets: list[defaultdict[tuple[int, int], list[tuple]]] = [defaultdict(list) for _ in range(3)]
        self.drill_buckets: defaultdict[tuple[int, int], list[tuple]] = defaultdict(list)
        self.via_buckets: defaultdict[tuple[int, int], list[tuple[int, float, float]]] = defaultdict(list)
        self.net_to_id: dict[str, int] = {"": 0}
        self.board_ok = bytearray(NX * NY)
        self.near_copper = [bytearray(NX * NY) for _ in range(3)]
        for iy in range(NY):
            y = wy(iy)
            row = iy * NX
            for ix in range(NX):
                if inside_board(wx(ix), y, 0.48):
                    self.board_ok[row + ix] = 1
        self._load_pads_and_drills()

    def _mark_near(self, li: int, x0: float, y0: float, x1: float, y1: float, margin: float = 0.55):
        ix0, ix1 = gx(x0 - margin), gx(x1 + margin)
        iy0, iy1 = gy(y0 - margin), gy(y1 + margin)
        nc = self.near_copper[li]
        span = ix1 - ix0 + 1
        ones = b"\x01" * span
        for iy in range(iy0, iy1 + 1):
            off = iy * NX + ix0
            nc[off:off + span] = ones

    def nid(self, net_name: str) -> int:
        if net_name not in self.net_to_id:
            self.net_to_id[net_name] = len(self.net_to_id)
        return self.net_to_id[net_name]

    def _add_to_buckets(self, li: int, x0: float, y0: float, x1: float, y1: float, obj):
        bx0, by0 = int(math.floor(x0)), int(math.floor(y0 + HW_Y))
        bx1, by1 = int(math.floor(x1)), int(math.floor(y1 + HW_Y))
        bk = self.buckets[li]
        for bx in range(bx0 - 1, bx1 + 2):
            for by in range(by0 - 1, by1 + 2):
                bk[(bx, by)].append(obj)

    def _add_drill_bucket(self, x0: float, y0: float, x1: float, y1: float, obj):
        bx0, by0 = int(math.floor(x0)), int(math.floor(y0 + HW_Y))
        bx1, by1 = int(math.floor(x1)), int(math.floor(y1 + HW_Y))
        for bx in range(bx0 - 1, bx1 + 2):
            for by in range(by0 - 1, by1 + 2):
                self.drill_buckets[(bx, by)].append(obj)

    def _load_pads_and_drills(self):
        for fp in self.board.GetFootprints():
            for p in fp.Pads():
                nn_raw = p.GetNetname()
                nn = nn_raw.lstrip("/") if nn_raw else ""
                nid = self.nid(nn)
                clr = route_cfg.net_clearance_mm(nn) if nn else route_cfg.DEFAULT_CLR_MM
                bb = p.GetBoundingBox()
                x0 = pcbnew.ToMM(bb.GetLeft()) - OX
                y0 = pcbnew.ToMM(bb.GetTop()) - OY
                x1 = pcbnew.ToMM(bb.GetRight()) - OX
                y1 = pcbnew.ToMM(bb.GetBottom()) - OY
                cx = pcbnew.ToMM(p.GetPosition().x) - OX
                cy = pcbnew.ToMM(p.GetPosition().y) - OY
                is_circ = p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE
                r = (x1 - x0) / 2.0 if is_circ else 0.0
                ds = p.GetDrillSize()
                has_drill = ds.x > 0 or ds.y > 0
                if p.IsOnCopperLayer():
                    mask = (1 if p.IsOnLayer(pcbnew.F_Cu) else 0) | (2 if p.IsOnLayer(pcbnew.B_Cu) else 0) | (4 if has_drill else 0)
                    obj = ("pad", nid, x0, y0, x1, y1, is_circ, cx, cy, r, clr)
                    for li in (0, 1, 2):
                        if mask & (1 << li):
                            self._add_to_buckets(li, x0, y0, x1, y1, obj)
                            self._mark_near(li, x0, y0, x1, y1)

                if has_drill:
                    rx = pcbnew.ToMM(ds.x) / 2.0
                    ry = pcbnew.ToMM(ds.y) / 2.0
                    dobj = (nid, cx, cy, rx, ry)
                    self._add_drill_bucket(cx - rx, cy - ry, cx + rx, cy + ry, dobj)
                    for li in (0, 1, 2):
                        self._mark_near(li, cx - rx, cy - ry, cx + rx, cy + ry)

    def add_track(self, nn: str, li: int, ax: float, ay: float, bx: float, by: float, width: float):
        nid = self.nid(nn)
        hw = width / 2.0
        clr = route_cfg.net_clearance_mm(nn)
        obj = ("trk", nid, ax, ay, bx, by, hw, clr)
        x0, y0, x1, y1 = min(ax, bx) - hw, min(ay, by) - hw, max(ax, bx) + hw, max(ay, by) + hw
        self._add_to_buckets(li, x0, y0, x1, y1, obj)
        self._mark_near(li, x0, y0, x1, y1)

    def add_via(self, nn: str, x: float, y: float):
        nid = self.nid(nn)
        clr = route_cfg.net_clearance_mm(nn)
        vobj = ("via", nid, x, y, VIA_R, clr)
        for li in (0, 1, 2):
            self._add_to_buckets(li, x - VIA_R, y - VIA_R, x + VIA_R, y + VIA_R, vobj)
            self._mark_near(li, x - VIA_R, y - VIA_R, x + VIA_R, y + VIA_R)
        dobj = (nid, x, y, route_cfg.VIA_DRILL_MM / 2.0, route_cfg.VIA_DRILL_MM / 2.0)
        self._add_drill_bucket(x - 0.15, y - 0.15, x + 0.15, y + 0.15, dobj)
        bx0, by0 = int(math.floor(x)), int(math.floor(y + HW_Y))
        for bx in range(bx0 - 1, bx0 + 2):
            for by in range(by0 - 1, by0 + 2):
                self.via_buckets[(bx, by)].append((nid, x, y))

    def point_clear(self, li: int, x: float, y: float, half_w: float, nid: int, clr_me: float,
                    check_drill: bool = True, ignore_own_pad: bool = False,
                    allow_afe_in2: bool = False) -> bool:
        edge_req = max(route_cfg.EDGE_CLR_MM, clr_me if clr_me > 0.3 else 0.0) + half_w
        if not inside_board(x, y, edge_req):
            return False
        if li == 2 and not allow_afe_in2 and in_afe_keepout(x, y, margin=0.35):
            return False
        bk_key = (int(math.floor(x)), int(math.floor(y + HW_Y)))
        seen = set()
        for obj in self.buckets[li].get(bk_key, ()):
            oid = id(obj)
            if oid in seen:
                continue
            seen.add(oid)
            onid = obj[1]
            if onid == nid:
                continue
            kind = obj[0]
            if kind == "pad":
                _, _, x0, y0, x1, y1, is_circ, cx, cy, r, oclr = obj
                req = max(clr_me, oclr) + half_w
                if is_circ:
                    if math.hypot(x - cx, y - cy) < r + req:
                        return False
                else:
                    if _rect_box_dist(x, y, x0, y0, x1, y1) < req:
                        return False
            elif kind == "trk":
                _, _, ax, ay, bx, by, ohw, oclr = obj
                req = max(clr_me, oclr) + half_w + ohw
                if _seg_dist(x, y, ax, ay, bx, by) < req:
                    return False
            else:  # via
                _, _, vx, vy, vr, oclr = obj
                req = max(clr_me, oclr) + half_w + vr
                if math.hypot(x - vx, y - vy) < req:
                    return False

        if check_drill:
            hclr = route_cfg.HOLE_CLR_MM + half_w
            for dobj in self.drill_buckets.get(bk_key, ()):
                dnid, dx, dy, rx, ry = dobj
                if dnid == nid and not ignore_own_pad:
                    continue
                if _rect_box_dist(x, y, dx - rx, dy - ry, dx + rx, dy + ry) < hclr:
                    return False
        return True

    def via_clear(self, x: float, y: float, nid: int, clr_me: float, min_via_pitch: float = 0.0) -> bool:
        if min_via_pitch > 0.0:
            bk_key = (int(math.floor(x)), int(math.floor(y + HW_Y)))
            for vnid, vx, vy in self.via_buckets.get(bk_key, ()):
                if vnid != nid and math.hypot(x - vx, y - vy) < min_via_pitch:
                    return False
        return self.point_clear(0, x, y, VIA_R + 0.005, nid, clr_me, check_drill=True, ignore_own_pad=True) and \
               self.point_clear(1, x, y, VIA_R + 0.005, nid, clr_me, check_drill=True, ignore_own_pad=True) and \
               self.point_clear(2, x, y, VIA_R + 0.005, nid, clr_me, check_drill=True, ignore_own_pad=True, allow_afe_in2=True)

    def seg_clear(self, li: int, ax: float, ay: float, bx: float, by: float,
                  half_w: float, nid: int, clr_me: float) -> bool:
        length = math.hypot(bx - ax, by - ay)
        steps = max(2, int(math.ceil(length / 0.035)) + 1)
        for k in range(steps):
            t = k / (steps - 1)
            px = ax + (bx - ax) * t
            py = ay + (by - ay) * t
            if not self.point_clear(li, px, py, half_w + 0.015, nid, clr_me, check_drill=True):
                return False
        return True


def _pad_breakout(fp: pcbnew.FOOTPRINT, p: pcbnew.PAD) -> tuple[float, float, float, float, int, bool]:
    """Return (cx, cy, ex_grid, ey_grid, layer_idx, needs_stub) for each pad."""
    cx = pcbnew.ToMM(p.GetPosition().x) - OX
    cy = pcbnew.ToMM(p.GetPosition().y) - OY
    li = 1 if fp.GetLayer() == pcbnew.B_Cu and not p.IsOnLayer(pcbnew.F_Cu) else 0
    ref = fp.GetReference()

    if p.GetDrillSize().x > 0 or ref in ("H1", "H2", "J2"):
        sx, sy = snap(cx, cy)
        return cx, cy, sx, sy, li, False

    fxc = pcbnew.ToMM(fp.GetPosition().x) - OX
    fyc = pcbnew.ToMM(fp.GetPosition().y) - OY

    ex, ey = cx, cy
    needs_stub = False
    if ref == "U1" and p.GetNumber().isdigit() and p.GetNumber() != "81":
        dx, dy = cx - fxc, cy - fyc
        pin_i = int(p.GetNumber())
        pin_in_side = (pin_i - 1) % 20
        if pin_i % 2 == 1:
            # Odd pins fan inward into U1 moat; stagger middle pins -1.10 / -1.90 (1.60 mm ring pitch!)
            if 3 <= pin_in_side <= 16 and pin_i % 4 == 3:
                step = -1.90
            else:
                step = -1.10
        else:
            step = 0.80
        if abs(dx) > abs(dy):
            ex = cx + math.copysign(1.0, dx) * step
        else:
            ey = cy + math.copysign(1.0, dy) * step
        needs_stub = True
    elif ref in ("U2", "U6", "U8", "D1", "D2", "J4", "J3"):
        bb = p.GetBoundingBox()
        w = pcbnew.ToMM(bb.GetWidth())
        h = pcbnew.ToMM(bb.GetHeight())
        if w > h * 1.3:
            sign = 1.0 if cx >= fxc else -1.0
            pad_clr = 0.15
            if ref == "J4" and p.GetNumber() != "MP":
                pad_clr = 0.45
                if int(p.GetNumber()) % 2 == 1:
                    sign = -1.0  # J4 odd signal pins fan inward under body -> 1.00 mm pitch
            elif ref == "U8" and p.GetNumber() in ("1", "4", "7", "10", "13"):
                # Keep U8 GND vias tucked under SOIC-14 body so outer x=90/98 corridors stay open
                sign = -1.0 if cx >= fxc else 1.0
                pad_clr = 0.65
            ex = cx + sign * (w / 2.0 + pad_clr)
            needs_stub = True
        elif h > w * 1.3:
            sign = 1.0 if cy >= fyc else -1.0
            if ref == "J3" and p.GetNumber() != "MP":
                sign = 1.0
            ey = cy + sign * (h / 2.0 + 0.15)
            needs_stub = True
    elif ref == "J1" and p.GetNumber() != "SH":
        num = p.GetNumber()
        if num in ("A4", "B4", "A6", "B6", "A7", "B7", "A9", "B9"):
            # Handled directly by _add_explicit_usb_bridges without duplicate Step-0 stubs
            ex = cx
            needs_stub = False
        else:
            ex = cx - 0.85
            needs_stub = True

    ex_g, ey_g = snap(ex, ey)
    return cx, cy, ex_g, ey_g, li, needs_stub


def _emit_track(board: pcbnew.BOARD, net_item, li: int, ax: float, ay: float, bx: float, by: float, width: float):
    if math.hypot(bx - ax, by - ay) < 0.002:
        return None
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(MM(OX + ax), MM(OY + ay)))
    t.SetEnd(pcbnew.VECTOR2I(MM(OX + bx), MM(OY + by)))
    t.SetWidth(MM(width))
    t.SetLayer(LAYERS[li])
    t.SetNet(net_item)
    board.Add(t)
    return t


def _emit_via(board: pcbnew.BOARD, net_item, x: float, y: float):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(MM(OX + x), MM(OY + y)))
    v.SetWidth(MM(route_cfg.VIA_DIAM_MM))
    v.SetDrill(MM(route_cfg.VIA_DRILL_MM))
    v.SetNet(net_item)
    board.Add(v)


def _bump_unrouted_pad(unrouted_cnt: list[int], ex_g: float, ey_g: float, delta: int, r_cells: int = 4) -> None:
    """Add `delta` (+1/-1) within `0.45 mm` of `(ex_g, ey_g)` so unrouted pin breakouts stay clear."""
    cx_i, cy_i = gx(ex_g), gy(ey_g)
    r2 = 20
    x0, x1 = max(0, cx_i - r_cells), min(NX - 1, cx_i + r_cells)
    y0, y1 = max(0, cy_i - r_cells), min(NY - 1, cy_i + r_cells)
    for iy in range(y0, y1 + 1):
        dy2 = (iy - cy_i) * (iy - cy_i)
        row = iy * NX
        for ix in range(x0, x1 + 1):
            if (ix - cx_i) * (ix - cx_i) + dy2 <= r2:
                unrouted_cnt[row + ix] += delta


def _astar_route(
    obs: ObstacleMap,
    starts: list[tuple[int, int, int]],   # (li, gx, gy)
    targets: list[tuple[int, int, int]],  # (li, gx, gy)
    nid: int,
    half_w: float,
    clr: float,
    allowed_layers: tuple[int, ...] = (0, 1, 2),
    search_pad_mm: float = 16.0,
    max_exp: int = 25000,
    unrouted_cnt: list[int] | None = None,
    min_via_pitch: float = 0.95,
    h_weight: float = 1.50,
) -> list[tuple[int, int, int]] | None:
    """Octilinear A* across (layer, gx, gy) supporting via transitions across F.Cu, B.Cu, and In2.Cu."""
    target_set = set(targets)
    if any(s in target_set for s in starts):
        for s in starts:
            if s in target_set:
                return [s]

    xs = [gx_ for _, gx_, _ in starts + targets]
    ys = [gy_ for _, _, gy_ in starts + targets]
    pad_cells = int(round(search_pad_mm / RES))
    x_min = max(0, min(xs) - pad_cells)
    x_max = min(NX - 1, max(xs) + pad_cells)
    y_min = max(0, min(ys) - pad_cells)
    y_max = min(NY - 1, max(ys) + pad_cells)

    # Aim heuristic at the CLOSEST target in `targets` to `starts` (never an average across far-apart clusters!)
    sx_mean = sum(gx_ for _, gx_, _ in starts) / len(starts)
    sy_mean = sum(gy_ for _, _, gy_ in starts) / len(starts)
    best_t = min(targets, key=lambda t: (t[1] - sx_mean) ** 2 + (t[2] - sy_mean) ** 2)
    tx_aim, ty_aim = best_t[1], best_t[2]

    def h(x: int, y: int) -> int:
        dx = abs(x - tx_aim)
        dy = abs(y - ty_aim)
        dmin, dmax = (dx, dy) if dx < dy else (dy, dx)
        return int(h_weight * (1414 * dmin + 1000 * (dmax - dmin)))

    clear_cache: dict[tuple[int, int, int], bool] = {}
    fast_safe = (half_w <= 0.125 and clr <= 0.18)
    bok = obs.board_ok
    nc = obs.near_copper

    def is_ok(li: int, x: int, y: int) -> bool:
        if fast_safe and li != 2:
            idx = y * NX + x
            if not bok[idx]:
                return False
            if not nc[li][idx]:
                return True
        k = (li, x, y)
        res = clear_cache.get(k)
        if res is None:
            res = obs.point_clear(li, wx(x), wy(y), half_w, nid, clr)
            clear_cache[k] = res
        return res

    via_cache: dict[tuple[int, int], bool] = {}

    def is_via_ok(li_cur: int, x: int, y: int) -> bool:
        fx, fy = wx(x), wy(y)
        k = (x, y)
        res = via_cache.get(k)
        if res is None:
            res = obs.via_clear(fx, fy, nid, clr, min_via_pitch=min_via_pitch)
            via_cache[k] = res
        return res

    dist: dict[tuple[int, int, int], int] = {}
    closed: set[tuple[int, int, int]] = set()
    prev: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    pq: list[tuple[int, int, int, int, int]] = []

    for s in starts:
        dist[s] = 0
        heapq.heappush(pq, (h(s[1], s[2]), 0, s[0], s[1], s[2]))

    expanded = 0
    allow_via = len(allowed_layers) > 1
    via_cost = 1200

    while pq:
        _, g, li, x, y = heapq.heappop(pq)
        st = (li, x, y)
        if st in target_set:
            path = [st]
            while st in prev:
                st = prev[st]
                path.append(st)
            path.reverse()
            return path
        if st in closed:
            continue
        closed.add(st)
        expanded += 1
        if expanded > max_exp:
            return None

        for dx, dy, step_cost in DIRS:
            nx, ny = x + dx, y + dy
            if not (x_min <= nx <= x_max and y_min <= ny <= y_max):
                continue
            nst = (li, nx, ny)
            pad_pen = 0
            if nst not in target_set:
                if not is_ok(li, nx, ny):
                    continue
                if dx and dy:
                    # Strictly check orthogonal shoulders so 45-degree segments never graze a neighbor
                    if not (is_ok(li, x + dx, y) and is_ok(li, x, y + dy)):
                        continue
                if unrouted_cnt and unrouted_cnt[ny * NX + nx] > 0:
                    pad_pen = 15000
            else:
                if dx and dy and not (is_ok(li, x + dx, y) and is_ok(li, x, y + dy)):
                    # Check segment safety when stepping diagonally onto target
                    if not obs.seg_clear(li, wx(x), wy(y), wx(nx), wy(ny), half_w, nid, clr):
                        continue
            if li == 0:
                dir_pen = 0 if dx == 0 else (200 if dy == 0 else 100)
            elif li == 1:
                dir_pen = 0 if dy == 0 else (200 if dx == 0 else 100)
            else:
                dir_pen = 0 if dy == 0 else (350 if dx == 0 else 175)
            ng = g + step_cost + dir_pen + pad_pen
            if ng < dist.get(nst, 1 << 60):
                dist[nst] = ng
                prev[nst] = st
                heapq.heappush(pq, (ng + h(nx, ny), ng, li, nx, ny))

        if allow_via and is_via_ok(li, x, y):
            for nli in allowed_layers:
                if nli == li:
                    continue
                nst = (nli, x, y)
                v_pen = 15000 if (unrouted_cnt and unrouted_cnt[y * NX + x] > 0 and nst not in target_set) else 0
                ng = g + via_cost + v_pen
                if ng < dist.get(nst, 1 << 60):
                    dist[nst] = ng
                    prev[nst] = st
                    heapq.heappush(pq, (ng + h(x, y), ng, nli, x, y))

    return None


def _collapse_path(path: list[tuple[int, int, int]]) -> tuple[list[tuple[int, float, float, float, float]], list[tuple[float, float]]]:
    segs, vias = [], []
    if len(path) <= 1:
        return segs, vias

    runs: list[tuple[int, list[tuple[int, int]]]] = []
    cur_li = path[0][0]
    cur_pts = [(path[0][1], path[0][2])]
    for li, x, y in path[1:]:
        if li != cur_li:
            runs.append((cur_li, cur_pts))
            vias.append((wx(cur_pts[-1][0]), wy(cur_pts[-1][1])))
            cur_li = li
            cur_pts = [(x, y)]
        else:
            cur_pts.append((x, y))
    runs.append((cur_li, cur_pts))

    for li, pts in runs:
        if len(pts) < 2:
            continue
        start = pts[0]
        dx0 = pts[1][0] - pts[0][0]
        dy0 = pts[1][1] - pts[0][1]
        for i in range(1, len(pts) - 1):
            dx1 = pts[i + 1][0] - pts[i][0]
            dy1 = pts[i + 1][1] - pts[i][1]
            if (dx1, dy1) != (dx0, dy0):
                segs.append((li, wx(start[0]), wy(start[1]), wx(pts[i][0]), wy(pts[i][1])))
                start = pts[i]
                dx0, dy0 = dx1, dy1
        segs.append((li, wx(start[0]), wy(start[1]), wx(pts[-1][0]), wy(pts[-1][1])))
    return segs, vias


def _find_local_plane_via(
    obs: ObstacleMap, x: float, y: float, li: int, nid: int,
    clr: float, poly: list[tuple[float, float]], half_w: float,
    reserved_pts: list[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    """Search spiraling outward from (x, y) for a clean drop-via inside `poly`."""
    candidates = []
    for r_step in range(0, 65):
        r = r_step * 0.10
        n_ang = 1 if r_step == 0 else 16
        for k in range(n_ang):
            ang = 2.0 * math.pi * k / n_ang
            vx = wx(gx(x + r * math.cos(ang)))
            vy = wy(gy(y + r * math.sin(ang)))
            candidates.append((r, vx, vy))

    seen = []
    seen_set = set()
    for _, vx, vy in candidates:
        if (vx, vy) in seen_set:
            continue
        seen_set.add((vx, vy))
        if in_afe_keepout(vx, vy, margin=0.35):
            continue
        if not point_in_poly(vx, vy, poly):
            continue
        if not obs.via_clear(vx, vy, nid, clr):
            continue
        if obs.seg_clear(li, x, y, vx, vy, half_w, nid, clr):
            seen.append((vx, vy))
    if not seen:
        return None
    if reserved_pts:
        # Prefer open via alleys (|vy| >= 4.70 or |vy| <= 0.75) away from unrouted signal breakouts
        clean = [(vx, vy) for vx, vy in seen if not any(abs(vx - rx) < 0.60 and abs(vy - ry) < 0.60 for rx, ry in reserved_pts)]
        for vx, vy in clean:
            if abs(vy) >= 4.70 or abs(vy) <= 0.75:
                return vx, vy
        if clean:
            return clean[0]
    for vx, vy in seen:
        if abs(vy) >= 4.70 or abs(vy) <= 0.75:
            return vx, vy
    return seen[0]


def _connect_plane_net(
    board: pcbnew.BOARD,
    obs: ObstacleMap,
    net_name: str,
    pad_entries: list[tuple[pcbnew.PAD, float, float, float, float, int, bool]],
    seed_anchors: list[tuple[int, int, int]] | None = None,
    reserved_pts: list[tuple[float, float]] | None = None,
    unrouted_cnt: list[int] | None = None,
) -> tuple[list, list[tuple[int, int, int]]]:
    _, poly = route_cfg.PLANE_ZONES[net_name]
    net_item = board.GetNetsByName()[NET(net_name)]
    nid = obs.nid(net_name)
    clr = route_cfg.net_clearance_mm(net_name)
    w = route_cfg.track_width_mm(net_name)
    half_w = w / 2.0

    connected_anchors: list[tuple[int, int, int]] = list(seed_anchors) if seed_anchors else []
    via_pts: list[tuple[int, float, float]] = []
    smd_entries = []

    for pad, cx, cy, ex_g, ey_g, li, needs_stub in pad_entries:
        # PTH pad already cuts through In1.Cu and In2.Cu inside the zone polygon!
        if pad.GetDrillSize().x > 0 and point_in_poly(cx, cy, poly) and not in_afe_keepout(cx, cy, 0.25):
            connected_anchors.append((li, gx(ex_g), gy(ey_g)))
            via_pts.append((0, ex_g, ey_g))
            via_pts.append((1, ex_g, ey_g))
            via_pts.append((2, ex_g, ey_g))
        else:
            smd_entries.append((pad, cx, cy, ex_g, ey_g, li, needs_stub))

    outside_nodes: list[tuple[float, float, float, float, int, bool]] = []
    for pad, cx, cy, ex_g, ey_g, li, needs_stub in smd_entries:
        stub_w = 0.13
        shared_hit = None
        for ali, ax, ay in via_pts:
            if ali == li and math.hypot(ax - ex_g, ay - ey_g) <= 2.8:
                if obs.seg_clear(li, ex_g, ey_g, ax, ay, stub_w / 2.0, nid, clr):
                    shared_hit = (ax, ay)
                    break
        if shared_hit is not None:
            ax, ay = shared_hit
            if not needs_stub and math.hypot(ex_g - cx, ey_g - cy) > 0.002:
                _emit_track(board, net_item, li, cx, cy, ex_g, ey_g, stub_w)
                obs.add_track(net_name, li, cx, cy, ex_g, ey_g, stub_w)
            if math.hypot(ax - ex_g, ay - ey_g) > 0.002:
                _emit_track(board, net_item, li, ex_g, ey_g, ax, ay, stub_w)
                obs.add_track(net_name, li, ex_g, ey_g, ax, ay, stub_w)
            connected_anchors.append((li, gx(ex_g), gy(ey_g)))
            continue

        res = _find_local_plane_via(obs, ex_g, ey_g, li, nid, clr, poly, stub_w / 2.0, reserved_pts=reserved_pts)
        if res is not None:
            vx, vy = res
            if not needs_stub and math.hypot(ex_g - cx, ey_g - cy) > 0.002:
                _emit_track(board, net_item, li, cx, cy, ex_g, ey_g, stub_w)
                obs.add_track(net_name, li, cx, cy, ex_g, ey_g, stub_w)
            if math.hypot(vx - ex_g, vy - ey_g) > 0.002:
                _emit_track(board, net_item, li, ex_g, ey_g, vx, vy, stub_w)
                obs.add_track(net_name, li, ex_g, ey_g, vx, vy, stub_w)
            _emit_via(board, net_item, vx, vy)
            obs.add_via(net_name, vx, vy)
            connected_anchors.append((li, gx(vx), gy(vy)))
            via_pts.append((0, vx, vy))
            via_pts.append((1, vx, vy))
            via_pts.append((2, vx, vy))
        else:
            outside_nodes.append((cx, cy, ex_g, ey_g, li, needs_stub))

    if not connected_anchors:
        px = sum(p[0] for p in poly) / len(poly)
        py = sum(p[1] for p in poly) / len(poly)
        res = _find_local_plane_via(obs, px, py, 0, nid, clr, poly, half_w)
        if res:
            vx, vy = res
            _emit_via(board, net_item, vx, vy)
            obs.add_via(net_name, vx, vy)
            connected_anchors.append((0, gx(vx), gy(vy)))

    failed = []
    remaining = list(outside_nodes)
    while remaining:
        remaining.sort(key=lambda it: min(math.hypot(it[2] - wx(ax), it[3] - wy(ay)) for _, ax, ay in connected_anchors))
        cx, cy, ex_g, ey_g, li, needs_stub = remaining.pop(0)
        routed = False

        # First try finding a nearby clear via inside `poly` and A*-routing directly to it
        local_vias = []
        for r_step in range(5, 65, 3):
            r = r_step * 0.10
            for k in range(16):
                ang = 2.0 * math.pi * k / 16
                vx, vy = snap(ex_g + r * math.cos(ang), ey_g + r * math.sin(ang))
                if not in_afe_keepout(vx, vy, margin=0.35) and point_in_poly(vx, vy, poly):
                    if obs.via_clear(vx, vy, nid, clr):
                        local_vias.append((r, vx, vy))
            if len(local_vias) >= 4:
                break

        if local_vias:
            tx_w = 0.14
            targets = [(k_li, gx(vx), gy(vy)) for _, vx, vy in local_vias[:4] for k_li in (0, 1, 2)]
            path = _astar_route(obs, [(li, gx(ex_g), gy(ey_g))], targets, nid, tx_w / 2.0, clr,
                                allowed_layers=(0, 1, 2), search_pad_mm=12.0, max_exp=30000,
                                unrouted_cnt=unrouted_cnt, min_via_pitch=0.0, h_weight=1.90)
            if path is not None:
                vx, vy = wx(path[-1][1]), wy(path[-1][2])
                if not needs_stub and math.hypot(ex_g - cx, ey_g - cy) > 0.002:
                    _emit_track(board, net_item, li, cx, cy, ex_g, ey_g, tx_w)
                    obs.add_track(net_name, li, cx, cy, ex_g, ey_g, tx_w)
                segs, vias = _collapse_path(path)
                for sli, sx0, sy0, sx1, sy1 in segs:
                    _emit_track(board, net_item, sli, sx0, sy0, sx1, sy1, tx_w)
                    obs.add_track(net_name, sli, sx0, sy0, sx1, sy1, tx_w)
                for pvx, pvy in vias:
                    _emit_via(board, net_item, pvx, pvy)
                    obs.add_via(net_name, pvx, pvy)
                _emit_via(board, net_item, vx, vy)
                obs.add_via(net_name, vx, vy)
                connected_anchors.append((li, gx(ex_g), gy(ey_g)))
                connected_anchors.append((0, gx(vx), gy(vy)))
                via_pts.append((0, vx, vy))
                via_pts.append((1, vx, vy))
                via_pts.append((2, vx, vy))
                routed = True

        if not routed:
            ranked = sorted(connected_anchors, key=lambda a: math.hypot(ex_g - wx(a[1]), ey_g - wy(a[2])))
            for anchor in ranked[:6]:
                tx_w = 0.14
                path = _astar_route(obs, [(li, gx(ex_g), gy(ey_g))], [anchor], nid, tx_w / 2.0, clr,
                                    allowed_layers=(0, 1, 2), search_pad_mm=65.0, max_exp=90000,
                                    unrouted_cnt=None, min_via_pitch=0.0, h_weight=1.90)
                if path is not None:
                    if not needs_stub and math.hypot(ex_g - cx, ey_g - cy) > 0.002:
                        _emit_track(board, net_item, li, cx, cy, ex_g, ey_g, tx_w)
                        obs.add_track(net_name, li, cx, cy, ex_g, ey_g, tx_w)
                    segs, vias = _collapse_path(path)
                    for sli, sx0, sy0, sx1, sy1 in segs:
                        _emit_track(board, net_item, sli, sx0, sy0, sx1, sy1, tx_w)
                        obs.add_track(net_name, sli, sx0, sy0, sx1, sy1, tx_w)
                    for vx, vy in vias:
                        _emit_via(board, net_item, vx, vy)
                        obs.add_via(net_name, vx, vy)
                    connected_anchors.append((li, gx(ex_g), gy(ey_g)))
                    for pt in path[::8]:
                        connected_anchors.append(pt)
                    routed = True
                    break
        if not routed:
            failed.append((net_name, round(cx, 2), round(cy, 2)))
    return failed, connected_anchors


def _add_explicit_usb_bridges(board: pcbnew.BOARD, obs: ObstacleMap):
    """Route J1 interleaved A6/B6 (DP) & A7/B7 (DM) bridges and VBUS_RAW jumper under USB-C shell."""
    nb = board.GetNetsByName()
    dm_net, dp_net = nb[NET("USB_CONN_DM")], nb[NET("USB_CONN_DP")]
    # J1 exact pad centers: A6=(125.255,+0.25), A7=(125.255,-0.25), B6=(125.255,-0.75), B7=(125.255,+0.75)
    # USB_CONN_DM: A7 -> (126.9, -0.25) -> via -> B.Cu -> (124.0, +0.75) -> via -> B7
    for ax, ay, bx, by, li in [
        (125.255, -0.25, 126.9, -0.25, 0),
        (126.9, -0.25, 126.9, 0.75, 1),
        (126.9, 0.75, 124.0, 0.75, 1),
        (124.0, 0.75, 125.255, 0.75, 0),
    ]:
        _emit_track(board, dm_net, li, ax, ay, bx, by, 0.20)
        obs.add_track("USB_CONN_DM", li, ax, ay, bx, by, 0.20)
    for vx, vy in [(126.9, -0.25), (124.0, 0.75)]:
        _emit_via(board, dm_net, vx, vy)
        obs.add_via("USB_CONN_DM", vx, vy)

    # USB_CONN_DP: A6 -> (127.7, +0.25) -> via -> B.Cu (y=-0.95 clears DM via @ y=-0.25) -> (124.0, -0.75) -> via -> B6
    for ax, ay, bx, by, li in [
        (125.255, 0.25, 127.7, 0.25, 0),
        (127.7, 0.25, 127.7, -0.95, 1),
        (127.7, -0.95, 124.0, -0.95, 1),
        (124.0, -0.95, 124.0, -0.75, 1),
        (124.0, -0.75, 125.255, -0.75, 0),
    ]:
        _emit_track(board, dp_net, li, ax, ay, bx, by, 0.20)
        obs.add_track("USB_CONN_DP", li, ax, ay, bx, by, 0.20)
    for vx, vy in [(127.7, 0.25), (124.0, -0.75)]:
        _emit_via(board, dp_net, vx, vy)
        obs.add_via("USB_CONN_DP", vx, vy)

    # VBUS_RAW: join J1 upper (125.255, -2.45) and lower (125.255, +2.45) on B.Cu at x=123.0 (clear of USB_CONN_DP/DM)
    vbus_net = nb[NET("VBUS_RAW")]
    for ax, ay, bx, by, li in [
        (125.255, -2.45, 124.1, -2.45, 0),
        (124.1, -2.45, 123.0, -1.35, 1),
        (123.0, -1.35, 123.0, 1.35, 1),
        (123.0, 1.35, 124.1, 2.45, 1),
        (124.1, 2.45, 125.255, 2.45, 0),
    ]:
        _emit_track(board, vbus_net, li, ax, ay, bx, by, 0.25)
        obs.add_track("VBUS_RAW", li, ax, ay, bx, by, 0.25)
    for vx, vy in [(124.1, -2.45), (124.1, 2.45)]:
        _emit_via(board, vbus_net, vx, vy)
        obs.add_via("VBUS_RAW", vx, vy)


class NetRouter:
    """Holds MST forest state per non-plane net so Phase 1 can route short edges first and Phase 3 finishes long ones."""

    def __init__(self, board: pcbnew.BOARD, obs: ObstacleMap, net_name: str,
                 pad_entries: list[tuple[pcbnew.PAD, float, float, float, float, int, bool]],
                 extra_seeds: dict[str, list[tuple[int, int, int]]] | None = None):
        self.board = board
        self.obs = obs
        self.net_name = net_name
        self.net_item = board.GetNetsByName()[NET(net_name)]
        self.nid = obs.nid(net_name)
        self.clr = route_cfg.net_clearance_mm(net_name)
        self.base_w = route_cfg.track_width_mm(net_name)

        self.nodes: list[tuple[int, int, int, float, float, bool]] = []
        seen_xy = set()
        for pad, cx, cy, ex_g, ey_g, li, needs_stub in pad_entries:
            k = (li, gx(ex_g), gy(ey_g))
            if k in seen_xy:
                continue
            seen_xy.add(k)
            self.nodes.append((k[0], k[1], k[2], cx, cy, needs_stub))

        self.parent = list(range(len(self.nodes)))
        self.comp_pts: list[list[tuple[int, int, int]]] = [[(n[0], n[1], n[2])] for n in self.nodes]
        self.bridged = [n[5] for n in self.nodes]

        if extra_seeds and net_name in extra_seeds:
            if net_name in ("USB_CONN_DP", "USB_CONN_DM", "VBUS_RAW"):
                idxs = [
                    i for i, n in enumerate(self.nodes)
                    if (net_name in ("USB_CONN_DP", "USB_CONN_DM") and wx(n[1]) > 124.5 and abs(wy(n[2])) < 1.5)
                    or (net_name == "VBUS_RAW" and wx(n[1]) > 124.5 and 2.3 <= abs(wy(n[2])) <= 2.6)
                ]
                if len(idxs) >= 2:
                    r0 = idxs[0]
                    for j in idxs[1:]:
                        self.parent[j] = r0
                        self.comp_pts[r0].extend(self.comp_pts[j])
                    self.comp_pts[r0].extend(extra_seeds[net_name])
                    for j in idxs:
                        self.bridged[j] = True
            else:
                # Pre-allocated horizontal B.Cu trunk forms a single connected anchor component for all pin clusters
                tpts = extra_seeds[net_name]
                t_idx = len(self.nodes)
                self.nodes.append((tpts[0][0], tpts[0][1], tpts[0][2], wx(tpts[0][1]), wy(tpts[0][2]), True))
                self.parent.append(t_idx)
                self.comp_pts.append(list(tpts))
                self.bridged.append(True)

    def _find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def route_up_to(self, max_dist_mm: float, unrouted_cnt: list[int] | None = None) -> list:
        if len(self.nodes) < 2:
            return []
        failed = []
        skipped_pairs: set[tuple[int, int]] = set()
        while True:
            # Find shortest inter-component edge <= max_dist_mm
            groups: dict[int, list[int]] = defaultdict(list)
            for i in range(len(self.nodes)):
                groups[self._find(i)].append(i)
            roots = list(groups.keys())
            if len(roots) <= 1:
                break

            best_pair = None
            best_d = max_dist_mm + 1e-6
            for a_i in range(len(roots)):
                ra = roots[a_i]
                pts_a = self.comp_pts[ra]
                for b_i in range(a_i + 1, len(roots)):
                    rb = roots[b_i]
                    pair_key = (min(ra, rb), max(ra, rb))
                    if pair_key in skipped_pairs:
                        continue
                    pts_b = self.comp_pts[rb]
                    # Check min point-to-point distance between component sets
                    d = min(
                        math.hypot(wx(pa[1]) - wx(pb[1]), wy(pa[2]) - wy(pb[2]))
                        for pa in pts_a[::max(1, len(pts_a) // 8)]
                        for pb in pts_b[::max(1, len(pts_b) // 8)]
                    )
                    if d < best_d:
                        best_d = d
                        best_pair = (ra, rb, d)

            if best_pair is None:
                break

            ra, rb, d_est = best_pair
            pts_u = self.comp_pts[ra]
            pts_v = self.comp_pts[rb]
            pad_mm = 15.0 if d_est < 8.0 else (30.0 if d_est < 22.0 else 90.0)
            cls = design.NETCLASS.get(self.net_name, "LV")
            min_w = 0.125 if cls in ("POWER", "USB") else 0.096
            w = max(min_w, self.base_w if d_est < 6.5 else min_w)

            # Sample nearest cluster targets around closest endpoints
            best_u = min(pts_u, key=lambda pu: min((wx(pu[1]) - wx(pv[1])) ** 2 + (wy(pu[2]) - wy(pv[2])) ** 2 for pv in pts_v))
            best_v = min(pts_v, key=lambda pv: (wx(best_u[1]) - wx(pv[1])) ** 2 + (wy(best_u[2]) - wy(pv[2])) ** 2)
            u_cands = [pu for pu in pts_u if math.hypot(wx(pu[1]) - wx(best_u[1]), wy(pu[2]) - wy(best_u[2])) <= 12.0] or [best_u]
            v_cands = [pv for pv in pts_v if math.hypot(wx(pv[1]) - wx(best_v[1]), wy(pv[2]) - wy(best_v[2])) <= 12.0] or [best_v]

            path = _astar_route(self.obs, u_cands, v_cands, self.nid, w / 2.0 + 0.002, self.clr,
                                allowed_layers=(0, 1, 2), search_pad_mm=pad_mm, max_exp=35000,
                                unrouted_cnt=unrouted_cnt, min_via_pitch=0.78, h_weight=1.55)
            if path is None:
                w = min_w
                path = _astar_route(self.obs, pts_u, pts_v, self.nid, w / 2.0 + 0.002, self.clr,
                                    allowed_layers=(0, 1, 2), search_pad_mm=100.0, max_exp=100000,
                                    unrouted_cnt=None, min_via_pitch=0.0, h_weight=2.10)
            if path is None:
                if max_dist_mm < 1e5:
                    # Leave unmerged during short-hop Phase 1b so Phase 2/3 retries with full board access
                    skipped_pairs.add((min(ra, rb), max(ra, rb)))
                    continue
                nu = self.nodes[groups[ra][0]]
                failed.append((self.net_name, round(nu[3], 2), round(nu[4], 2)))
                # Merge so we don't spin repeatedly on the same disconnected component pair
                self.parent[rb] = ra
                self.comp_pts[ra].extend(self.comp_pts[rb])
                continue

            for end_pt in (path[0], path[-1]):
                for nidx, n in enumerate(self.nodes):
                    if not self.bridged[nidx] and (n[0], n[1], n[2]) == end_pt:
                        ex_g, ey_g = wx(n[1]), wy(n[2])
                        if math.hypot(ex_g - n[3], ey_g - n[4]) > 0.002:
                            _emit_track(self.board, self.net_item, n[0], n[3], n[4], ex_g, ey_g, w)
                            self.obs.add_track(self.net_name, n[0], n[3], n[4], ex_g, ey_g, w)
                        self.bridged[nidx] = True

            segs, vias = _collapse_path(path)
            for sli, sx0, sy0, sx1, sy1 in segs:
                _emit_track(self.board, self.net_item, sli, sx0, sy0, sx1, sy1, w)
                self.obs.add_track(self.net_name, sli, sx0, sy0, sx1, sy1, w)
            for vx, vy in vias:
                _emit_via(self.board, self.net_item, vx, vy)
                self.obs.add_via(self.net_name, vx, vy)

            self.parent[rb] = ra
            self.comp_pts[ra].extend(self.comp_pts[rb])
            self.comp_pts[ra].extend(path[::4])
        return failed


def _run_routing_pass(promoted: list[str]) -> tuple[pcbnew.BOARD, list, int]:
    from hw import gen_pcb
    board = gen_pcb.build()
    board.BuildConnectivity()
    obs = ObstacleMap(board)

    net_pads: dict[str, list] = defaultdict(list)
    for fp in board.GetFootprints():
        for p in fp.Pads():
            nn = p.GetNetname()
            if not nn or nn.startswith("unconnected-"):
                continue
            cx, cy, ex_g, ey_g, li, needs_stub = _pad_breakout(fp, p)
            net_pads[nn.lstrip("/")].append((p, cx, cy, ex_g, ey_g, li, needs_stub))

    nets_by_name = board.GetNetsByName()
    for net_name, entries in net_pads.items():
        net_item = nets_by_name[NET(net_name)]
        clr = route_cfg.net_clearance_mm(net_name)
        nid = obs.nid(net_name)
        w_stub = 0.125
        seen_stub = set()
        for p, cx, cy, ex_g, ey_g, li, needs_stub in entries:
            if not needs_stub or math.hypot(ex_g - cx, ey_g - cy) < 0.002:
                continue
            sk = (li, round(cx, 3), round(cy, 3))
            if sk in seen_stub:
                continue
            seen_stub.add(sk)
            if obs.seg_clear(li, cx, cy, ex_g, ey_g, w_stub / 2.0, nid, clr):
                _emit_track(board, net_item, li, cx, cy, ex_g, ey_g, w_stub)
                obs.add_track(net_name, li, cx, cy, ex_g, ey_g, w_stub)

    _add_explicit_usb_bridges(board, obs)
    extra_seeds = {
        "USB_CONN_DM": [(1, gx(124.0), gy(0.75)), (1, gx(125.4), gy(0.75)), (1, gx(126.9), gy(0.25))],
        "USB_CONN_DP": [(1, gx(124.0), gy(-0.75)), (1, gx(125.8), gy(-0.95)), (1, gx(127.7), gy(0.25))],
        "VBUS_RAW": [(1, gx(124.1), gy(-2.45)), (1, gx(123.0), gy(0.0)), (1, gx(124.1), gy(2.45))],
    }

    routers = {
        nn: NetRouter(board, obs, nn, net_pads[nn], extra_seeds)
        for nn in route_cfg.ORDERED_NETS
        if nn in net_pads and nn not in route_cfg.PLANE_ZONES
    }

    unrouted_cnt = [0] * (NX * NY)
    net_keepout_pts: dict[str, list[tuple[float, float]]] = {}
    for nn, entries in net_pads.items():
        pts = []
        for p, cx, cy, ex_g, ey_g, li, needs_stub in entries:
            if p.GetDrillSize().x > 0:
                continue
            ref = p.GetParentFootprint().GetReference()
            if ref == "J1" and p.GetNumber() in ("A4", "B4", "A6", "B6", "A7", "B7", "A9", "B9", "SH"):
                continue
            pts.append((ex_g, ey_g))
            _bump_unrouted_pad(unrouted_cnt, ex_g, ey_g, +1)
        net_keepout_pts[nn] = pts

    failures = []
    # Phase 1: Route short signal hops (<= 6.5 mm) while protecting all other unrouted pad breakouts
    for nn in route_cfg.ORDERED_NETS:
        if nn in routers:
            for ex_g, ey_g in net_keepout_pts.get(nn, ()):
                _bump_unrouted_pad(unrouted_cnt, ex_g, ey_g, -1)
            routers[nn].route_up_to(6.5, unrouted_cnt=unrouted_cnt)
            r = routers[nn]
            if len({r._find(i) for i in range(len(r.nodes))}) > 1:
                for ex_g, ey_g in net_keepout_pts.get(nn, ()):
                    _bump_unrouted_pad(unrouted_cnt, ex_g, ey_g, +1)

    # Phase 2: Route ALL remaining signal nets across F.Cu (vertical) and B.Cu (horizontal)
    head = [n for n in promoted if n in routers]
    tail = [n for n in route_cfg.ORDERED_NETS if n in routers and n not in head]
    released = set()
    for nn in head + tail:
        if nn not in released:
            for ex_g, ey_g in net_keepout_pts.get(nn, ()):
                _bump_unrouted_pad(unrouted_cnt, ex_g, ey_g, -1)
            released.add(nn)
        failures.extend(routers[nn].route_up_to(1e6, unrouted_cnt=unrouted_cnt))

    # Phase 3: Connect all 4 plane nets LAST with via sharing and A* fallback
    reserved_pts = [
        (e[3], e[4])
        for nn2, entries2 in net_pads.items()
        if nn2 not in route_cfg.PLANE_ZONES
        for e in entries2
    ]
    for nn in ("GND", "AGND"):
        if nn in net_pads:
            for ex_g, ey_g in net_keepout_pts.get(nn, ()):
                _bump_unrouted_pad(unrouted_cnt, ex_g, ey_g, -1)
            f, _ = _connect_plane_net(board, obs, nn, net_pads[nn], reserved_pts=reserved_pts, unrouted_cnt=unrouted_cnt)
            failures.extend(f)

    board.BuildConnectivity()
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    return board, failures, len(net_pads)


def main():
    promoted: list[str] = []
    board = None
    failures = []
    total_nets = 0
    for attempt in range(1, 5):
        board, failures, total_nets = _run_routing_pass(promoted)
        print(f"pass {attempt}: routed {total_nets} nets, failures={len(failures)}: {failures[:20]}", flush=True)
        if not failures:
            break
        new_bad = [f[0] for f in failures if f[0] not in promoted]
        if not new_bad:
            break
        promoted = new_bad + promoted

    pcbnew.SaveBoard(str(PCB_FILE), board)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
