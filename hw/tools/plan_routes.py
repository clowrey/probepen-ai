"""Planning aid that writes hw/routes.py.

hw/route.py only draws the explicit tracks listed in hw/routes.py; nothing is auto-routed at build
time. This tool is how that list is produced: for each net in PLAN (in order) it searches a
0.05 mm grid for an octilinear path on F.Cu / B.Cu that keeps the planned clearance to everything
already placed, straightens it into long 0/45/90-degree legs, and writes the result as plain
t(...) / b(...) lines. The inner layers are left to the planes. Re-run it after changing the
placement in hw/gen_pcb.py or the PLAN below, then review the tracks and run the DRC tests.

    kicad-python hw/gen_pcb.py
    kicad-python hw/tools/dump_pads.py hw/probepen_ai.kicad_pcb /tmp/pp_pads.json
    python -m hw.tools.plan_routes /tmp/pp_pads.json          # writes hw/routes.py
    kicad-python hw/route.py

Coordinates: board datum mm, KiCad Y-down (same frame as PLACE in hw/gen_pcb.py).
"""
from __future__ import annotations

import heapq
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hardware.pcb_datum import PCB as DATUM, half_width_at, point_in_pcb  # noqa: E402
from hw import design  # noqa: E402

RES = 0.05
BL = float(DATUM["length"])
HWY = float(DATUM["width"]) / 2.0
GW = int(round(BL / RES)) + 1
GH = int(round(2 * HWY / RES)) + 1
EDGE_CLR = 0.33          # copper to board edge / unplated hole
HV_CLR = 0.45            # high-voltage divider nets to anything else (rule: 0.40)
ANA_CLR = 0.22           # ANALOG net class to anything else (net class clearance: 0.20)
VIA_HALO = 0.10          # extra keep-off round a via so its hole clears neighbouring copper by 0.25
VIA = (0.40, 0.20)       # via pad / drill: the smallest the design rules allow
VIA_R = VIA[0] / 2.0
PLANE_NETS = ("GND", "AGND", "+3V3_DIG", "+3V3_ANA")
HV_NETS = ("CH1_TIP", "CH1_HV_MID", "CH2_TIP", "CH2_HV_MID")
UNC = 30000              # id of pads that belong to no net
WIDE_PAD_REFS = ("U3", "U4", "U6")   # footprints whose pads need 0.20 mm to every other net
ISLAND_X1, ISLAND_Y1 = 83.2, 4.45    # analog island (AGND / +3V3_ANA), see hw/gen_pcb.py
KEEPOUT = (1.0, 26.2, 6.0)           # x0, x1, |y|: no inner planes under the high-impedance input


def gi(x: float) -> int:
    return int(round(x / RES))


def gj(y: float) -> int:
    return int(round((y + HWY) / RES))


def mx(i: int) -> float:
    return round(i * RES, 3)


def my(j: int) -> float:
    return round(j * RES - HWY, 3)


# ---------------------------------------------------------------------------------------------
# What to route, in order. Each entry: (net or list of nets, options).
#   w       track width (mm)                      clr   clearance to other nets (mm)
#   layers  "F", "B" or "FB"                      via   cost of a via in mm of extra track
#   margin  search window around the net (mm)
# ---------------------------------------------------------------------------------------------
ANALOG = {
    "CH1_TIP", "CH1_HV_MID", "CH1_VDIV", "CH1_VBUF", "CH1_INV", "CH1_OUT", "CH1_SW_LO",
    "CH2_TIP", "CH2_HV_MID", "CH2_VDIV", "CH2_VBUF", "CH2_INV", "CH2_OUT", "CH2_SW_LO",
    "ADCA_INP", "ADCB_INP", "REF_MID", "REF_T1", "REF_T2", "ADC_VREF", "+3V0_REF",
    "+5V_ANA_IN", "LED1_A", "LED2_A",
}
NOSE_NETS = {"CH1_TIP", "CH1_HV_MID", "CH1_VDIV", "CH2_TIP", "CH2_HV_MID", "CH2_VDIV",
             "LED1_A", "LED2_A", "+3V0_REF"}
XTAL_NETS = {"XIN", "XOUT_RAW", "XOUT_XTAL", "GND"}
NOSE_X = 32.0            # only NOSE_NETS may be routed nose-side of this, on either layer
ANA_X1 = 73.0            # digital nets stay off F.Cu between NOSE_X and here (north of ANA_Y1)
ANA_Y1 = 4.45


def _bus(prefix: str) -> list[str]:
    return [f"{prefix}{i}" for i in range(8)]


PLAN: list[tuple] = [
    # High-voltage input and the high-impedance divider nodes
    (["CH1_TIP", "CH2_TIP"], dict(w=0.25, clr=HV_CLR, layers="F")),
    (["CH1_HV_MID", "CH2_HV_MID"], dict(w=0.22, clr=HV_CLR, layers="F")),
    (["CH1_VDIV", "CH2_VDIV"], dict(w=0.15, clr=0.20, layers="FB", via=6.0)),
    # Gain stage: summing nodes first, then their neighbours
    (["CH1_INV", "CH2_INV"], dict(w=0.15, clr=0.15, layers="F")),
    (["CH1_SW_LO", "CH2_SW_LO"], dict(w=0.15, clr=0.15, layers="F")),
    (["CH1_OUT", "CH2_OUT"], dict(w=0.18, clr=0.15, layers="F")),
    (["ADCA_INP", "ADCB_INP", "ADC_VREF"], dict(w=0.15, clr=0.15, layers="F")),
    (["CH1_VBUF", "CH2_VBUF"], dict(w=0.18, clr=0.18, layers="F")),
    (["REF_T1", "REF_T2"], dict(w=0.15, clr=0.15, layers="F")),
    ("REF_MID", dict(w=0.15, clr=0.15, layers="FB", via=8.0)),
    # Core regulator, crystal, USB: short and on the top layer
    ("VREG_LX", dict(w=0.20, clr=0.14, layers="F")),
    ("VREG_AVDD", dict(w=0.20, clr=0.14, layers="F")),
    # ADC data buses, innermost lane first, top layer only (over solid ground)
    (_bus("ADCA_D"), dict(w=0.13, clr=0.14, layers="F", margin=5.0)),
    (_bus("ADCB_D"), dict(w=0.13, clr=0.14, layers="F", margin=5.0)),
    (["ADC_ENCA", "ADC_ENCB"], dict(w=0.15, clr=0.15, layers="F")),
    ("ADC_CLK_MCU", dict(w=0.15, clr=0.15, layers="FB", via=3.0)),
    ("+1V1_CORE", dict(w=0.22, clr=0.14, layers="FB", via=3.0)),
    ("ADC_AVDD", dict(w=0.20, clr=0.14, layers="FB")),
    ("STUBS", dict(refs=("U1", "U2"))),               # supply-pin vias of the fine-pitch parts
    (["CH1_DC_SENSE", "CH2_DC_SENSE", "VBUS_SENSE"], dict(w=0.15, clr=0.15, layers="FB", via=2.0, margin=20.0)),
    (["XIN", "XOUT_RAW", "XOUT_XTAL"], dict(w=0.15, clr=0.15, layers="F")),
    # USB full-speed pair: the second line runs alongside the first at a 0.15 mm gap
    ("USB_DP", dict(w=0.18, clr=0.14, layers="F")),
    ("USB_DM", dict(w=0.18, clr=0.14, layers="F", hug="USB_DP")),
    # Everything else
    (["LCD_SCK", "LCD_MOSI", "LCD_CS_N", "LCD_DC", "LCD_RST_N"], dict(w=0.15, clr=0.14, layers="FB", via=2.0)),
    (["LCD_BLK_GATE_N", "LCD_BLK_SW"], dict(w=0.15, clr=0.14, layers="FB", via=2.0)),
    ("LCD_LEDA", dict(w=0.25, clr=0.14, layers="FB", via=2.0)),
    (["ADC_S1", "ADC_S2", "CH1_GAIN_LO", "CH2_GAIN_LO"], dict(w=0.15, clr=0.14, layers="FB", via=2.0)),
    (["POD_CH0", "POD_CH1", "POD_CH2", "POD_CH3"], dict(w=0.15, clr=0.14, layers="FB", via=3.0)),
    (["POD_CH0_RAW", "POD_CH1_RAW", "POD_CH2_RAW", "POD_CH3_RAW"], dict(w=0.15, clr=0.14, layers="FB", via=3.0)),
    (["QSPI_SS_N", "BOOT_KEY", "RUN_N", "SWCLK", "SWDIO"], dict(w=0.15, clr=0.14, layers="FB", via=2.0)),
    (["BTN_DOWN_N", "BTN_UP_N", "BTN_MODE_N", "BTN_ASK_AI_N"], dict(w=0.15, clr=0.14, layers="FB", via=2.0, margin=20.0)),
    (["LED1_A", "LED2_A"], dict(w=0.20, clr=0.20, layers="FB", via=2.0, margin=20.0)),
    # USB connector side and power entry
    ("USB_CONN_DP", dict(w=0.18, clr=0.14, layers="FB", via=3.0)),
    ("USB_CONN_DM", dict(w=0.18, clr=0.14, layers="FB", via=3.0, hug="USB_CONN_DP")),
    (["USB_CC1", "USB_CC2"], dict(w=0.15, clr=0.14, layers="FB")),
    ("VBUS_RAW", dict(w=0.35, clr=0.15, layers="FB", via=2.0)),
    ("+5V_VBUS", dict(w=0.35, clr=0.15, layers="FB", via=2.0, margin=20.0)),
    ("+5V_ANA_IN", dict(w=0.30, clr=0.15, layers="FB", via=3.0)),
    ("+3V3_POD", dict(w=0.30, clr=0.15, layers="FB")),
    ("+3V0_REF", dict(w=0.20, clr=0.15, layers="FB", via=4.0, margin=20.0)),
]


# ---------------------------------------------------------------------------------------------
class Board:
    def __init__(self, pads_json: str):
        data = json.loads(Path(pads_json).read_text(encoding="utf8"))
        self.pads = data["pads"]
        self.net_id: dict[str, int] = {}
        for n in design.NETS:
            self.net_id[n] = len(self.net_id) + 1
        self.hv_ids = [self.net_id[n] for n in HV_NETS]
        self.ana_names = {n for n, c in design.NETCLASS.items() if c == "ANALOG"}
        self.ana_ids = [self.net_id[n] for n in self.ana_names]
        self.own = [np.zeros((GH, GW), np.int16) for _ in range(2)]
        self.padmask = np.zeros((GH, GW), bool)

        inside = np.zeros((GH, GW), bool)
        ys = np.array([my(j) for j in range(GH)])
        for i in range(GW):
            x = mx(i)
            if x < 2.2 or x > BL - 2.2:
                inside[:, i] = [point_in_pcb(x, float(y)) for y in ys]
            else:
                inside[:, i] = np.abs(ys) <= half_width_at(x)
        jj, ii = np.mgrid[0:GH, 0:GW]
        for h in data["holes"]:
            r = h["d"] / 2.0
            inside &= np.hypot(ii * RES - h["x"], jj * RES - HWY - h["y"]) > r
        inside[0, :] = inside[-1, :] = False
        inside[:, 0] = inside[:, -1] = False
        self.dist_out = ndimage.distance_transform_edt(inside) * RES
        self.xs = ii * RES
        self.ys = jj * RES - HWY

        idx: dict[tuple[str, str], int] = {}
        for p in self.pads:
            key = (p["ref"], p["num"])
            p["k"] = idx.get(key, 0)
            idx[key] = p["k"] + 1
            nid = self.net_id.get(p["net"], UNC)
            i0, i1 = int(math.ceil(p["x0"] / RES - 1e-6)), int(math.floor(p["x1"] / RES + 1e-6))
            j0, j1 = int(math.ceil((p["y0"] + HWY) / RES - 1e-6)), int(math.floor((p["y1"] + HWY) / RES + 1e-6))
            for layer, on in ((0, p["f"]), (1, p["b"])):
                if on:
                    self.own[layer][j0:j1 + 1, i0:i1 + 1] = nid
            self.padmask[j0:j1 + 1, i0:i1 + 1] = True
        self.pad_dist = ndimage.distance_transform_edt(~self.padmask) * RES
        self.wide = [np.zeros((GH, GW), np.int16) for _ in range(2)]
        for layer in range(2):
            for p in self.pads:
                if p["ref"] in WIDE_PAD_REFS and (p["f"] if layer == 0 else p["b"]):
                    i0, i1 = int(math.ceil(p["x0"] / RES - 1e-6)), int(math.floor(p["x1"] / RES + 1e-6))
                    j0, j1 = int(math.ceil((p["y0"] + HWY) / RES - 1e-6)), int(math.floor((p["y1"] + HWY) / RES + 1e-6))
                    self.wide[layer][j0:j1 + 1, i0:i1 + 1] = self.net_id.get(p["net"], UNC)
        island = (self.xs < ISLAND_X1 - 0.5) & ((self.ys < ISLAND_Y1 - 0.5) | (self.xs < 30.5))
        digital = (self.xs > ISLAND_X1 + 0.5) | ((self.ys > ISLAND_Y1 + 0.5) & (self.xs > 31.5))
        keep = (self.xs > KEEPOUT[0] - 0.5) & (self.xs < KEEPOUT[1] + 0.5) & (np.abs(self.ys) < KEEPOUT[2] + 0.5)
        self.plane_ok = {"AGND": island & ~keep, "+3V3_ANA": island & ~keep & (self.xs > KEEPOUT[1] + 0.5),
                         "GND": digital, "+3V3_DIG": digital}
        self.base_own = [o.copy() for o in self.own]
        self.base_pad = self.padmask.copy()
        self.trk = [np.zeros((GH, GW), np.int32) for _ in range(2)]   # which unit drew each track / via cell
        self.units: dict[str, dict] = {}
        self.keys: list[str] = [""]
        self.cur: dict | None = None
        self.replay = False
        self.pad_dirty = False
        self.xtal = None
        ys1 = [p for p in self.pads if p["ref"] == "Y1"]
        if ys1:
            x0, x1 = min(p["x0"] for p in ys1) - 0.5, max(p["x1"] for p in ys1) + 0.5
            y0, y1 = min(p["y0"] for p in ys1) - 0.4, max(p["y1"] for p in ys1) + 0.4
            self.xtal = (self.xs > x0) & (self.xs < x1) & (self.ys > y0) & (self.ys < y1)
        self.out: dict[str, list[dict]] = {}
        self.failed: list[str] = []

    # -- obstacle maps for one net -------------------------------------------------------------
    # -- units: one net (or one plane stub) that can be removed and drawn again ----------------------
    def begin(self, key: str, net: str, crit: bool):
        if key not in self.units:
            self.keys.append(key)
        self.units[key] = {"net": net, "items": [], "paths": [], "crit": crit, "idx": self.keys.index(key)}
        self.cur = self.units[key]

    def rebuild(self, crit_only: bool = False, without: tuple = ()):
        self.own = [o.copy() for o in self.base_own]
        self.padmask = self.base_pad.copy()
        self.trk = [np.zeros((GH, GW), np.int32) for _ in range(2)]
        self.replay = True
        keep = self.cur
        for key, u in self.units.items():
            if key in without or (crit_only and not u["crit"]):
                continue
            self.cur = u
            nid = self.net_id[u["net"]]
            for it in u["items"]:
                if it[0] == "t":
                    self.stamp_track(nid, it[1], it[2], it[3])
                else:
                    self.stamp_via(nid, it[1], it[2])
        self.cur = keep
        self.replay = False
        self.pad_dirty = True

    def masks(self, net: str, w: float, clr: float):
        if self.pad_dirty:
            self.pad_dist = ndimage.distance_transform_edt(~self.padmask) * RES
            self.pad_dirty = False
        nid = self.net_id[net]
        own_c = HV_CLR if net in HV_NETS else max(clr, ANA_CLR) if net in self.ana_names else clr
        blk, dists = [], []
        for layer in range(2):
            own = self.own[layer]
            foreign = (own != 0) & (own != nid)
            d = ndimage.distance_transform_edt(~foreign) * RES - own_c
            for ids, c in ((self.hv_ids, HV_CLR), (self.ana_ids, ANA_CLR)):
                if c <= own_c:
                    continue
                sel = np.isin(own, ids) & (own != nid)
                if sel.any():
                    d = np.minimum(d, ndimage.distance_transform_edt(~sel) * RES - c)
            wsel = (self.wide[layer] != 0) & (self.wide[layer] != nid)
            if wsel.any():
                d = np.minimum(d, ndimage.distance_transform_edt(~wsel) * RES - 0.215)
            b = d < w / 2 - 1e-6                      # d = distance to foreign copper beyond the clearance
            b |= self.dist_out < w / 2 + EDGE_CLR
            if net not in NOSE_NETS:
                b |= self.xs < NOSE_X
            if net not in ANALOG and layer == 0:
                # digital nets stay off the top layer of the analog section, except to leave their own pads
                own_pad = (own == nid) & self.padmask
                near = ndimage.distance_transform_edt(~own_pad) * RES < 1.3 if own_pad.any() else own_pad
                b |= (self.xs >= NOSE_X) & (self.xs < ANA_X1) & (self.ys < ANA_Y1) & ~near
            if layer == 0 and net not in XTAL_NETS and self.xtal is not None:
                b |= self.xtal
            blk.append(b)
            dists.append(d)
        need = VIA_R + VIA_HALO
        via_ok = (dists[0] >= need) & (dists[1] >= need) & (self.dist_out >= VIA_R + EDGE_CLR + 0.05)
        via_ok &= self.pad_dist >= VIA_R + 0.15
        via_ok &= ~blk[0] & ~blk[1]
        return blk, via_ok

    # -- A* over (layer, j, i) -----------------------------------------------------------------
    def search(self, blk, via_ok, starts, tgt, layers, via_cost, win, cheap=None):
        i0, j0, i1, j1 = win
        b = [m.tobytes() for m in blk]
        v = via_ok.tobytes()
        t = [m.tobytes() for m in tgt]
        tj, ti = np.nonzero(tgt[0] | tgt[1])
        if not len(ti):
            return None
        tb = (int(ti.min()), int(ti.max()), int(tj.min()), int(tj.max()))
        vc = int(via_cost / RES * 10)
        ch = [m.tobytes() for m in cheap] if cheap is not None else None
        hk = 5 if ch else 10                          # heuristic scale stays admissible with cheap cells

        def h(j, i):
            dx = max(tb[0] - i, 0, i - tb[1])
            dy = max(tb[2] - j, 0, j - tb[3])
            return (10 * max(dx, dy) + 4 * min(dx, dy)) * hk // 10

        pq, g, prev = [], {}, {}
        for layer, j, i in starts:
            node = (layer * GH + j) * GW + i
            g[node] = 0
            prev[node] = -1
            heapq.heappush(pq, (h(j, i), 0, node))
        moves = ((0, 1, 10), (0, -1, 10), (1, 0, 10), (-1, 0, 10), (1, 1, 14), (1, -1, 14), (-1, 1, 14), (-1, -1, 14))
        done = set()
        while pq:
            _, gc, node = heapq.heappop(pq)
            if node in done:
                continue
            done.add(node)
            layer, rem = divmod(node, GH * GW)
            j, i = divmod(rem, GW)
            if t[layer][rem]:
                path = []
                while node != -1:
                    layer, rem = divmod(node, GH * GW)
                    j, i = divmod(rem, GW)
                    path.append((layer, j, i))
                    node = prev[node]
                return path[::-1]
            bl = b[layer]
            for dj, di, c in moves:
                nj, ni = j + dj, i + di
                if ni < i0 or ni > i1 or nj < j0 or nj > j1:
                    continue
                nrem = nj * GW + ni
                if bl[nrem] and not t[layer][nrem]:
                    continue
                nn = layer * GH * GW + nrem
                ng = gc + (c // 2 if ch and ch[layer][nrem] else c)
                if ng < g.get(nn, 1 << 60):
                    g[nn] = ng
                    prev[nn] = node
                    heapq.heappush(pq, (ng + h(nj, ni), ng, nn))
            if len(layers) == 2 and v[rem]:
                ol = 1 - layer
                nn = ol * GH * GW + rem
                ng = gc + vc
                if ng < g.get(nn, 1 << 60):
                    g[nn] = ng
                    prev[nn] = node
                    heapq.heappush(pq, (ng + h(j, i), ng, nn))
        return None

    # -- straighten a cell path into long octilinear legs ------------------------------------------
    @staticmethod
    def _line_ok(m, a, b):
        (j, i), (bj, bi) = a, b
        dj = (bj > j) - (bj < j)
        di = (bi > i) - (bi < i)
        while (j, i) != (bj, bi):
            j += dj
            i += di
            if m[j, i]:
                return False
        return True

    def _link(self, m, a, b, prefer_diag_first):
        dj, di = b[0] - a[0], b[1] - a[1]
        d = min(abs(dj), abs(di))
        diag = (int(math.copysign(d, dj)) if dj else 0, int(math.copysign(d, di)) if di else 0)
        e1 = (a[0] + diag[0], a[1] + diag[1])                    # diagonal first
        e2 = (b[0] - diag[0], b[1] - diag[1])                    # straight first
        opts = [e1, e2] if prefer_diag_first else [e2, e1]
        for e in opts:
            if self._line_ok(m, a, e) and self._line_ok(m, e, b):
                return [p for p in (e, b) if p != a]
        return None

    def pull(self, m, cells):
        out = [cells[0]]
        i, n = 0, len(cells)
        flip = False
        while i < n - 1:
            last, fail, j = None, None, i + 1
            while True:
                got = self._link(m, cells[i], cells[j], flip)
                if got is None:
                    fail = j
                    break
                last = (j, got)
                if j == n - 1:
                    break
                j = min(n - 1, i + 2 * (j - i))
            if last is None:
                last = (i + 1, [cells[i + 1]])
            elif fail is not None:
                lo, hi = last[0], fail
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    got = self._link(m, cells[i], cells[mid], flip)
                    if got is None:
                        hi = mid
                    else:
                        lo, last = mid, (mid, got)
            out += last[1]
            i = last[0]
            flip = not flip
        res = [out[0]]
        for p in out[1:]:
            if p == res[-1]:
                continue
            if len(res) >= 2:
                a, c = res[-2], res[-1]
                if (c[0] - a[0]) * (p[1] - c[1]) == (c[1] - a[1]) * (p[0] - c[0]) and \
                        (c[0] - a[0]) * (p[0] - c[0]) + (c[1] - a[1]) * (p[1] - c[1]) > 0:
                    res[-1] = p
                    continue
            res.append(p)
        return res

    # -- stamping --------------------------------------------------------------------------------
    @staticmethod
    def _cells(poly):
        cells = [poly[0]]
        for a, b in zip(poly, poly[1:]):
            j, i = a
            dj = (b[0] > j) - (b[0] < j)
            di = (b[1] > i) - (b[1] < i)
            while (j, i) != b:
                j += dj
                i += di
                cells.append((j, i))
        return cells

    def stamp_track(self, nid, layer, cells, w):
        r = int(math.ceil(w / 2 / RES)) + 1
        js = [c[0] for c in cells]
        is_ = [c[1] for c in cells]
        j0, j1 = max(0, min(js) - r), min(GH - 1, max(js) + r)
        i0, i1 = max(0, min(is_) - r), min(GW - 1, max(is_) + r)
        loc = np.ones((j1 - j0 + 1, i1 - i0 + 1), bool)
        loc[np.array(js) - j0, np.array(is_) - i0] = False
        near = ndimage.distance_transform_edt(loc) * RES <= w / 2 + 1e-6
        sub = self.own[layer][j0:j1 + 1, i0:i1 + 1]
        new = near & (sub == 0)
        sub[new] = nid
        self.trk[layer][j0:j1 + 1, i0:i1 + 1][new] = self.cur["idx"]
        if not self.replay:
            self.cur["items"].append(("t", layer, cells, w))

    def stamp_via(self, nid, j, i):
        r = int(math.ceil((VIA_R + VIA_HALO) / RES)) + 1
        j0, j1, i0, i1 = max(0, j - r), min(GH - 1, j + r), max(0, i - r), min(GW - 1, i + r)
        jj, ii = np.mgrid[j0:j1 + 1, i0:i1 + 1]
        disc = np.hypot(jj - j, ii - i) * RES <= VIA_R + VIA_HALO + 1e-6
        for layer in range(2):
            sub = self.own[layer][j0:j1 + 1, i0:i1 + 1]
            sub[disc] = nid
            self.trk[layer][j0:j1 + 1, i0:i1 + 1][disc] = self.cur["idx"]
        self.padmask[j0:j1 + 1, i0:i1 + 1] |= disc
        self.pad_dirty = True
        if not self.replay:
            self.cur["items"].append(("v", j, i))

    # -- one net ---------------------------------------------------------------------------------
    def pins_of(self, net):
        best: dict[tuple[str, str], dict] = {}
        for p in self.pads:
            if p["net"] != net:
                continue
            key = (p["ref"], p["num"])
            area = (p["x1"] - p["x0"]) * (p["y1"] - p["y0"])
            if key not in best or area > best[key]["_a"]:
                best[key] = dict(p, _a=area)
        return list(best.values())

    def route(self, net, w=0.15, clr=0.14, layers="FB", via=2.5, margin=7.0, first=None, hug=None, gap=0.15):
        nid = self.net_id[net]
        pins = self.pins_of(net)
        fails: list[str] = []
        if len(pins) < 2:
            return fails
        lay = [0] * ("F" in layers) + [1] * ("B" in layers)
        blk, via_ok = self.masks(net, w, clr)
        for layer in range(2):
            if layer not in lay:
                blk[layer][:] = True
        if len(lay) < 2:
            # a single-layer net may still need a via to reach a pad on the other side
            other = [p for p in pins if not (p["f"] if lay[0] == 0 else p["b"])]
            if other:
                blk, via_ok = self.masks(net, w, clr)
                lay = [0, 1]
        cheap = None
        if hug and hug in self.net_id:
            # second half of a pair: running at exactly `gap` from its partner costs half
            cheap = []
            for layer in range(2):
                d = ndimage.distance_transform_edt(self.own[layer] != self.net_id[hug]) * RES
                cheap.append(np.abs(d - (gap + w / 2)) <= RES * 0.8)
        tree = [np.zeros((GH, GW), bool) for _ in range(2)]
        pins.sort(key=lambda p: (p["x"], p["y"]))
        if first:
            pins.sort(key=lambda p: (p["ref"], p["num"]) != first)
        done = [pins[0]]
        todo = pins[1:]

        def seed(p):
            return [(layer, gj(p["y"]), gi(p["x"])) for layer, on in ((0, p["f"]), (1, p["b"])) if on]

        for layer, j, i in seed(pins[0]):
            tree[layer][j, i] = True
        paths = self.cur["paths"]
        while todo:
            todo.sort(key=lambda p: min(math.hypot(p["x"] - q["x"], p["y"] - q["y"]) for q in done))
            p = todo.pop(0)
            tj, ti = np.nonzero(tree[0] | tree[1])
            found = None
            for mrg in (margin, margin * 2.5, 200.0):
                i0 = max(1, min(int(ti.min()), gi(p["x"])) - int(mrg / RES))
                i1 = min(GW - 2, max(int(ti.max()), gi(p["x"])) + int(mrg / RES))
                j0 = max(1, min(int(tj.min()), gj(p["y"])) - int(mrg / RES))
                j1 = min(GH - 2, max(int(tj.max()), gj(p["y"])) + int(mrg / RES))
                found = self.search(blk, via_ok, seed(p), tree, lay, via, (i0, j0, i1, j1), cheap)
                if found:
                    break
            if not found:
                fails.append(f"{net}: {p['ref']}.{p['num']}")
                done.append(p)
                continue
            # split into single-layer runs joined by vias
            n_before = len(paths)
            runs, cur = [], [found[0]]
            for a in found[1:]:
                if a[0] != cur[-1][0]:
                    runs.append(cur)
                    cur = [a]
                else:
                    cur.append(a)
            runs.append(cur)
            for k, run in enumerate(runs):
                layer = run[0][0]
                poly = self.pull(blk[layer], [(j, i) for _, j, i in run])
                cells = self._cells(poly)
                self.stamp_track(nid, layer, cells, w)
                for j, i in cells:
                    tree[layer][j, i] = True
                pts: list = [(mx(i), my(j)) for j, i in poly]
                if k == 0:
                    pts[0] = (p["ref"], p["num"], p["k"])
                rec = {"layer": "FB"[layer], "w": w, "pts": pts, "vias": []}
                if k < len(runs) - 1:
                    vj, vi = run[-1][1], run[-1][2]
                    rec["vias"].append((mx(vi), my(vj)))
                    self.stamp_via(nid, vj, vi)
                    tree[0][vj, vi] = tree[1][vj, vi] = True
                if len(poly) > 1 or rec["vias"]:
                    paths.append(rec)
            # the far end may have landed on another pad's centre: name it
            end = found[-1]
            if len(paths) > n_before and not paths[-1]["vias"]:
                for q in done:
                    if any((layer, j, i) == end for layer, j, i in seed(q)):
                        paths[-1]["pts"][-1] = (q["ref"], q["num"], q["k"])
            done.append(p)
            for layer, j, i in seed(p):
                tree[layer][j, i] = True
        return fails

    def plane_stub(self, net, p, w=0.20, clr=0.14, reach=3.0):
        """Short stub + via from one pad of a plane net to its plane. Returns [] or a failure note."""
        if p["f"] and p["b"]:
            return []                                 # through-hole pads reach the plane themselves
        nid = self.net_id[net]
        layer = 0 if p["f"] else 1
        blk, via_ok = self.masks(net, w, clr)
        tgt = [np.zeros((GH, GW), bool) for _ in range(2)]
        i0, i1 = max(1, gi(p["x"] - reach)), min(GW - 2, gi(p["x"] + reach))
        j0, j1 = max(1, gj(p["y"] - reach)), min(GH - 2, gj(p["y"] + reach))
        win = np.zeros((GH, GW), bool)
        win[j0:j1 + 1, i0:i1 + 1] = True
        same_via = (self.own[0] == nid) & (self.own[1] == nid) & self.padmask & ~self.base_pad
        centre = ndimage.distance_transform_edt(same_via) * RES >= VIA_R        # centres of this net's vias
        tgt[layer] = ((via_ok | centre) & self.plane_ok[net]) & win
        if not tgt[layer].any():
            return [f"{net}: {p['ref']}.{p['num']} (no via site)"]
        other = [np.ones((GH, GW), bool), np.ones((GH, GW), bool)]
        other[layer] = blk[layer]
        found = self.search(other, np.zeros((GH, GW), bool), [(layer, gj(p["y"]), gi(p["x"]))], tgt, [layer], 0, (i0, j0, i1, j1))
        if not found:
            return [f"{net}: {p['ref']}.{p['num']}"]
        poly = self.pull(blk[layer], [(j, i) for _, j, i in found])
        cells = self._cells(poly)
        vj, vi = poly[-1]
        new = not centre[vj, vi]
        self.stamp_track(nid, layer, cells, w)
        pts: list = [(mx(i), my(j)) for j, i in poly]
        pts[0] = (p["ref"], p["num"], p["k"])
        if new:
            self.stamp_via(nid, vj, vi)
        if len(pts) == 1:
            pts.append((mx(vi), my(vj)))
        self.cur["paths"].append({"layer": "FB"[layer], "w": w, "pts": pts, "vias": [(mx(vi), my(vj))] if new else []})
        return []

    # -- output ----------------------------------------------------------------------------------
    def write(self, dst: Path):
        def fmt(pt):
            if isinstance(pt[0], str):
                return f'("{pt[0]}", "{pt[1]}"' + (f", {pt[2]})" if pt[2] else ")")
            return f"({pt[0]:.2f}, {pt[1]:.2f})"

        out: dict[str, list] = {}
        for u in self.units.values():
            out.setdefault(u["net"], []).extend(u["paths"])
        lines = [HEADER]
        for net, paths in out.items():
            if not paths:
                continue
            lines.append(f"\n# {net}")
            for r in paths:
                fn = "t" if r["layer"] == "F" else "b"
                args = [f'"{net}"'] + [fmt(p) for p in r["pts"]] + [f"w={r['w']:.2f}"]
                if r["vias"]:
                    args.append("vias=[" + ", ".join(f"({x:.2f}, {y:.2f})" for x, y in r["vias"]) + "]")
                lines.append(f"{fn}(" + ", ".join(args) + ")")
        dst.write_text("\n".join(lines) + "\n", encoding="utf8", newline="\n")

    def preview(self, dst: str, x0: float, x1: float, scale: int = 3):
        from PIL import Image
        i0, i1 = gi(x0), gi(x1)
        f, b = self.own[0][:, i0:i1 + 1] != 0, self.own[1][:, i0:i1 + 1] != 0
        pad = self.padmask[:, i0:i1 + 1]
        img = np.full((GH, i1 - i0 + 1, 3), 255, np.uint8)
        img[self.dist_out[:, i0:i1 + 1] <= 0] = (60, 60, 60)
        img[b] = (120, 160, 255)
        img[f] = (230, 90, 80)
        img[f & b] = (150, 60, 160)
        img[pad & f] = (150, 30, 30)
        img[pad & b & ~f] = (40, 70, 170)
        Image.fromarray(img).resize(((i1 - i0 + 1) * scale, GH * scale), Image.NEAREST).save(dst)


HEADER = '''"""Hand-planned 4-layer routes for ProbePen-AI (138 x 17.60 mm, JLC04161H-7628).

Signals run on F.Cu and B.Cu only; In1.Cu (GND / AGND) and In2.Cu (+3V3_DIG / +3V3_ANA) stay solid
planes, and hw/route.py connects plane pads with its own short stubs and vias. The tracks below were
planned with hw/tools/plan_routes.py (see its PLAN table) and are drawn exactly as listed.
Run: kicad-python hw/gen_pcb.py && kicad-python hw/route.py
"""
from __future__ import annotations

ROUTES: dict = {}
VIA = (%.2f, %.2f)


def t(net, *pts, w=0.15, layer="F", vias=()):
    ROUTES.setdefault(net, []).append({"layer": layer, "w": w, "pts": list(pts), "vias": list(vias), "via": VIA})


def b(net, *pts, w=0.15, vias=()):
    t(net, *pts, w=w, layer="B", vias=vias)
''' % VIA


CRITICAL_GROUPS = 24     # the first PLAN groups (analog chain, regulator, crystal, USB, buses) are
                         # never moved to make room for later nets


def solve(pads_json: str, max_rips: int = 2):
    bd = Board(pads_json)
    jobs: dict[str, dict] = {}
    order: list[str] = []
    def add_stubs(refs, crit):
        for net in PLANE_NETS:
            for p in sorted(bd.pins_of(net), key=lambda q: (q["x"], q["y"])):
                if refs is not None and p["ref"] not in refs:
                    continue
                if refs is None and net in ("GND", "AGND") and p["ref"] not in STUB_GND_REFS and not p["ref"].startswith("C"):
                    continue                           # the pours pick these up
                key = f"{net}@{p['ref']}.{p['num']}.{p['k']}"
                if key not in jobs:
                    jobs[key] = {"kind": "stub", "net": net, "pin": p, "crit": crit}
                    order.append(key)

    for g, (nets, opt) in enumerate(PLAN):
        if nets == "STUBS":
            add_stubs(opt["refs"], True)
            continue
        for net in ([nets] if isinstance(nets, str) else nets):
            if net in design.NETS:
                jobs[net] = {"kind": "net", "net": net, "opt": opt, "crit": g < CRITICAL_GROUPS}
                order.append(net)
    add_stubs(None, False)

    def run(key):
        j = jobs[key]
        bd.begin(key, j["net"], j["crit"])
        if j["kind"] == "net":
            return bd.route(j["net"], **j["opt"])
        return bd.plane_stub(j["net"], j["pin"])

    def footprint(key):
        """Cells the unit would use if nothing movable were in its way, widened by its clearance."""
        j = jobs[key]
        saved = bd.units.pop(key, None)
        bd.rebuild(crit_only=not j["crit"])
        fails = run(key)
        marks = [np.zeros((GH, GW), bool) for _ in range(2)]
        for it in bd.units[key]["items"]:
            if it[0] == "t":
                js, is_ = zip(*it[2])
                marks[it[1]][np.array(js), np.array(is_)] = True
            else:
                marks[0][it[1], it[2]] = marks[1][it[1], it[2]] = True
        del bd.units[key]
        if saved:
            bd.units[key] = saved
        bd.rebuild()
        if fails:
            return None
        return [ndimage.distance_transform_edt(~m) * RES < 0.75 if m.any() else m for m in marks]

    queue = list(order)
    rips: dict[str, int] = {}
    failed: dict[str, list[str]] = {}
    steps = 0
    while queue and steps < 900:
        steps += 1
        key = queue.pop(0)
        fails = run(key)
        if fails and rips.get(key, 0) < max_rips:
            near = footprint(key)
            victims: list[str] = []
            if near is not None:
                ids = set()
                for layer in range(2):
                    ids |= set(np.unique(bd.trk[layer][near[layer]]).tolist())
                for i in sorted(ids - {0}):
                    k2 = bd.keys[i]
                    if k2 != key and k2 in bd.units and (jobs[key]["crit"] or not jobs[k2]["crit"]):
                        victims.append(k2)
            if victims:
                rips[key] = rips.get(key, 0) + 1
                for k2 in victims + [key]:
                    bd.units.pop(k2, None)
                bd.rebuild()
                fails = run(key)
                queue = victims + [q for q in queue if q not in victims]
                print(f"  {key}: moved {len(victims)} ({', '.join(victims[:6])}{'...' if len(victims) > 6 else ''}) -> {'ok' if not fails else 'still open'}", flush=True)
        if fails:
            failed[key] = fails
        else:
            failed.pop(key, None)
    bd.failed = [f for v in failed.values() for f in v]
    return bd


STUB_GND_REFS = ("U1", "U2", "U3", "U4", "U5", "U6", "U9", "U10", "D1", "D2", "J3", "J4", "Y1", "R16", "Q1")


def main(pads_json: str):
    bd = solve(pads_json)
    planned = {n for nets, _ in PLAN if nets != "STUBS" for n in ([nets] if isinstance(nets, str) else nets)}
    missing = sorted(set(design.NETS) - planned - set(PLANE_NETS))
    if missing:
        print("nets not in PLAN:", missing)
    print("FAILED:", bd.failed or "none")
    bd.write(ROOT / "hw" / "routes.py")
    for k, (a, c) in enumerate(((0, 47), (45, 93), (90, 138))):
        bd.preview(f"/tmp/ppv/plan_{k}.png", a, c)


if __name__ == "__main__":
    main(*sys.argv[1:])
