"""Add hand-planned octilinear tracks with rounded bends (hw/routes.py), fill zones, and stitch ground.

Run with local KiCad Python after hw/gen_pcb.py:
    ~/.local/bin/kicad-python hw/route.py

Route points are board-datum coordinates (mm, origin nose-tip center, +X toward USB-C tail,
Y centered -7.5..+7.5 mm, Y-down in KiCad) or pad references:
    ("U1", "17") = centre of pad 17 of U1; ("J1", "SH", 2) = third pad numbered SH of J1.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hardware.pcb_datum import point_in_pcb  # noqa: E402
from hw.gen_pcb import (  # noqa: E402
    AFE_X0, AFE_X1, AFE_Y0, AFE_Y1, HW_Y, L, MM, NET, OX, OY, PCB_FILE, R_NOSE, R_TAIL,
)

LAYERS = {
    "F": pcbnew.F_Cu,
    "B": pcbnew.B_Cu,
    "In1": pcbnew.In1_Cu,
    "In2": pcbnew.In2_Cu,
    "In3": pcbnew.In3_Cu,
    "In4": pcbnew.In4_Cu,
}

DEFAULT_VIA = (0.40, 0.20)
CUT = 0.0  # extra length (mm) of a corner's 45-degree run beyond its planned arc's tangent


class Resolver:
    """Resolve pad references ('U1', '17'[, idx]) and board-datum (x, y) mm coordinates to KiCad VECTOR2I."""

    def __init__(self, board: pcbnew.BOARD):
        self.pads: dict[tuple[str, str], list[pcbnew.VECTOR2I]] = {}
        self.objs: dict[tuple[str, str], list[pcbnew.PAD]] = {}
        for fp in board.GetFootprints():
            ref = fp.GetReference()
            for p in fp.Pads():
                key = (ref, p.GetNumber())
                self.pads.setdefault(key, []).append(p.GetPosition())
                self.objs.setdefault(key, []).append(p)

    def pad(self, pt, net_name: str):
        """Return the pad a path endpoint lands on (named ref/pin or overlapping pad on the same net)."""
        if isinstance(pt[0], str):
            return self.objs[(pt[0], pt[1])][pt[2] if len(pt) > 2 else 0]
        q = self(pt)
        for pads in self.objs.values():
            for p in pads:
                if p.GetNetname() == net_name and p.HitTest(q):
                    return p
        return None

    def __call__(self, pt) -> pcbnew.VECTOR2I:
        if isinstance(pt[0], str):
            ref, num = pt[0], pt[1]
            idx = pt[2] if len(pt) > 2 else 0
            pos = self.pads[(ref, num)][idx]
            return pcbnew.VECTOR2I(pos.x, pos.y)
        return pcbnew.VECTOR2I(MM(OX + pt[0]), MM(OY + pt[1]))


def bend_radius(width_mm: float) -> float:
    """Target radius of a track bend: 0.35 mm for fine signals, 2.0 x the width for wider tracks."""
    return max(0.35, 2.0 * width_mm)


def corner_radius(width_mm: float) -> float:
    """Radius of the small arc at each 45-degree bend: 0.20 mm for signals, 1.2 x the width for wider tracks."""
    return max(0.20, 1.2 * width_mm)


def _turn(u, v) -> float:
    """Angle in radians between two direction vectors (0 = straight on)."""
    if u is None or v is None:
        return 0.0
    return abs(math.remainder(math.atan2(v[1], v[0]) - math.atan2(u[1], u[0]), 2 * math.pi))


def _chamfers(pts, width: float, fixed, max_d, radii, cuts=None):
    """Cut free 90-degree corners into two 45-degree bends sized so parallel concentric lanes stay spaced."""
    n = len(pts)
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(n - 1)]
    out_p, out_f, out_m, out_r = [pts[0]], [fixed[0]], [max_d[0]], [None]
    small = corner_radius(width)
    for i in range(1, n - 1):
        p = pts[i]
        u1 = ((pts[i - 1][0] - p[0]) / seg[i - 1], (pts[i - 1][1] - p[1]) / seg[i - 1])
        u2 = ((pts[i + 1][0] - p[0]) / seg[i], (pts[i + 1][1] - p[1]) / seg[i])
        turn = math.pi - math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        if not fixed[i] and abs(turn - math.pi / 2) < math.radians(1):
            want = radii[i] if radii[i] is not None else bend_radius(width)
            room1 = seg[i - 1] * (0.5 if i - 1 > 0 and not fixed[i - 1] else 0.9)
            room2 = seg[i] * (0.5 if i + 1 < n - 1 and not fixed[i + 1] else 0.9)
            cut = cuts[i] if cuts and cuts[i] is not None else CUT
            d = min(MM(want) * (2 - math.sqrt(2)) + MM(cut), room1, room2, max_d[i])
            if d > MM(0.08):
                for u in (u1, u2):
                    out_p.append((p[0] + u[0] * d, p[1] + u[1] * d))
                    out_f.append(False)
                    out_m.append(max_d[i] - d)
                    out_r.append(small)
                continue
        out_p.append(p)
        out_f.append(fixed[i])
        out_m.append(max_d[i])
        out_r.append(small if abs(turn - math.pi / 4) < math.radians(1) else radii[i])
    out_p.append(pts[-1])
    out_f.append(fixed[-1])
    out_m.append(max_d[-1])
    out_r.append(None)

    keep = [0]
    for j in range(1, len(out_p)):
        if math.dist(out_p[j], out_p[keep[-1]]) < MM(0.01):
            k = keep[-1]
            out_f[k] = out_f[k] or out_f[j]
            out_m[k] = min(out_m[k], out_m[j])
            if j == len(out_p) - 1:
                out_p[k] = out_p[j]
            continue
        keep.append(j)
    return (
        [out_p[j] for j in keep],
        [out_f[j] for j in keep],
        [out_m[j] for j in keep],
        [out_r[j] for j in keep],
    )


def octilinear(pts, start=None, end=None, inside=None):
    """Snap every non-0/45/90-degree leg into a straight leg plus a 45-degree leg, choosing the smoother turn."""
    out = [(pts[0], 0)]
    prev = tuple(start) if start else None
    for i in range(1, len(pts)):
        a, c = out[-1][0], pts[i]
        dx, dy = c[0] - a[0], c[1] - a[1]
        if math.hypot(dx, dy) < 1:
            continue
        off = abs(math.remainder(math.degrees(math.atan2(dy, dx)), 45))
        nxt = None
        if i + 1 < len(pts):
            nxt = (pts[i + 1][0] - c[0], pts[i + 1][1] - c[1])
        elif end:
            nxt = tuple(end)
        if math.hypot(dx, dy) * math.sin(math.radians(off)) > MM(0.025):
            m = min(abs(dx), abs(dy))
            diag = (math.copysign(m, dx), math.copysign(m, dy))
            straight = (dx - diag[0], dy - diag[1])
            best = None
            for first, second in ((straight, diag), (diag, straight)):
                q = (a[0] + first[0], a[1] + first[1])
                cost = _turn(prev, first) + _turn(first, second) + _turn(second, nxt)
                if inside and (inside(0, q) and i == 1 or inside(len(pts) - 1, q) and i == len(pts) - 1):
                    cost += 10
                if best is None or cost < best[0] - 1e-6:
                    best = (cost, q, second)
            out.append((best[1], None))
            prev = best[2]
        else:
            prev = (dx, dy)
        out.append((c, i))
    return out


def _fillets(pts, width: float, fixed, max_d=None, radii=None):
    """Compute circular arc fillet tangent points (t1, mid, t2) in nm at free bends."""
    n = len(pts)
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(n - 1)]
    plan = {}
    for i in range(1, n - 1):
        if fixed[i]:
            continue
        p = pts[i]
        u1 = ((pts[i - 1][0] - p[0]) / seg[i - 1], (pts[i - 1][1] - p[1]) / seg[i - 1])
        u2 = ((pts[i + 1][0] - p[0]) / seg[i], (pts[i + 1][1] - p[1]) / seg[i])
        cos_phi = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        phi = math.acos(cos_phi)
        if phi < math.radians(5) or phi > math.radians(179):
            continue
        tan_half_turn = math.tan((math.pi - phi) / 2)
        room1 = seg[i - 1] * (0.5 if i - 1 > 0 and not fixed[i - 1] else 0.9)
        room2 = seg[i] * (0.5 if i + 1 < n - 1 and not fixed[i + 1] else 0.9)
        room = min(room1, room2, max_d[i] if max_d else float("inf"))
        want = radii[i] if radii and i < len(radii) and radii[i] is not None else bend_radius(width)
        r = min(MM(want), room / tan_half_turn)
        d = r * tan_half_turn
        if d < MM(0.01):
            continue
        t1 = (p[0] + u1[0] * d, p[1] + u1[1] * d)
        t2 = (p[0] + u2[0] * d, p[1] + u2[1] * d)
        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        if bl < 1e-6:
            continue
        to_mid = r / math.sin(phi / 2) - r
        mid = (p[0] + bis[0] / bl * to_mid, p[1] + bis[1] / bl * to_mid)
        plan[i] = (t1, mid, t2)
    return plan


def add_routes(board: pcbnew.BOARD, routes: dict):
    """Emit all hand-planned tracks, arcs, and vias from routes dict onto the board."""
    nets = board.GetNetsByName()
    res = Resolver(board)

    def key(v):
        return (v.x, v.y)

    anchors = {}
    for net_name, paths in routes.items():
        a = anchors.setdefault(net_name, [])
        for path in paths:
            pts = [key(res(p)) for p in path.get("pts", [])]
            if pts:
                a += [(pts[0], id(path)), (pts[-1], id(path))]
                a += [(q, id(path)) for q in pts[1:-1]]
            a += [(key(res(v)), None) for v in path.get("vias", [])]

    for net_name, paths in routes.items():
        net = nets[NET(net_name)]
        for path in paths:
            layer, width = path.get("layer", "F"), path.get("w", 0.15)
            raw = path.get("pts", [])
            pts = []
            for j, (p, q) in enumerate(zip(raw, [res(p) for p in raw])):
                if not pts or key(q) != pts[-1][1]:
                    pts.append((p, key(q), j))

            first = res.pad(pts[0][0], net.GetNetname()) if pts else None
            last = res.pad(pts[-1][0], net.GetNetname()) if pts else None

            def inside(i, q):
                pad = first if i == 0 else last
                return pad is not None and pad.HitTest(pcbnew.VECTOR2I(int(q[0]), int(q[1])))

            oct_pts = octilinear([q for _, q, _ in pts], path.get("start"), path.get("end"), inside) if pts else []
            xy = [q for q, _ in oct_pts]
            others = [a for a, owner in anchors[net_name] if owner != id(path)]
            fixed, max_d, radii = [], [], []
            for i, (q, src_i) in enumerate(oct_pts):
                src = pts[src_i][0] if src_i is not None else None
                dists = [math.dist(a, q) for a in others]
                fixed.append(
                    (src is not None and isinstance(src[0], str))
                    or i in (0, len(oct_pts) - 1)
                    or any(dd < MM(0.05) for dd in dists)
                )
                max_d.append(min([dd - MM(width) for dd in dists if dd < MM(3 * bend_radius(width))], default=float("inf")))
                r = path.get("radii")
                radii.append(r[pts[src_i][2]] if r and src_i is not None and pts[src_i][2] < len(r) else None)

            if len(xy) > 2:
                cuts = path.get("cuts")
                if cuts:
                    cuts = [cuts[pts[src_i][2]] if src_i is not None and pts[src_i][2] < len(cuts) else None for _, src_i in oct_pts]
                xy, fixed, max_d, radii = _chamfers(xy, width, fixed, max_d, radii, cuts)
            plan = _fillets(xy, width, fixed, max_d, radii)

            def vec(p):
                return pcbnew.VECTOR2I(int(round(p[0])), int(round(p[1])))

            def add(item):
                item.SetWidth(MM(width))
                item.SetLayer(LAYERS[layer])
                item.SetNet(net)
                board.Add(item)

            def line(a, b):
                if math.dist(a, b) < MM(0.02):
                    return
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(vec(a))
                t.SetEnd(vec(b))
                add(t)

            def arc(a, m, b):
                if math.dist(a, b) < MM(0.02):
                    return
                t = pcbnew.PCB_ARC(board)
                t.SetStart(vec(a))
                t.SetMid(vec(m))
                t.SetEnd(vec(b))
                add(t)

            cur = xy[0] if xy else None
            for i in range(1, len(xy)):
                end = plan[i][0] if i in plan else xy[i]
                if vec(cur) != vec(end):
                    line(cur, end)
                if i in plan:
                    t1, mid, t2 = plan[i]
                    arc(t1, mid, t2)
                    cur = t2
                else:
                    cur = xy[i]

            size, drill = path.get("via", DEFAULT_VIA)
            for v in path.get("vias", []):
                via = pcbnew.PCB_VIA(board)
                via.SetPosition(res(v))
                via.SetWidth(MM(size))
                via.SetDrill(MM(drill))
                via.SetNet(net)
                board.Add(via)


def _inside_board(x: float, y: float, margin: float) -> bool:
    return point_in_pcb(x, y, margin=margin)


def _seg_point_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    d2 = vx * vx + vy * vy
    if d2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / d2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _box_point_dist(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx = 0.0 if x0 <= px <= x1 else (x0 - px if px < x0 else px - x1)
    dy = 0.0 if y0 <= py <= y1 else (y0 - py if py < y0 else py - y1)
    return math.hypot(dx, dy)


def place_plane_vias(board: pcbnew.BOARD) -> tuple[int, list[tuple[str, str, str]]]:
    """Deterministically drop clean 0.40/0.20 mm vias (or share nearby vias) for unconnected plane pads.

    Covers GND (In1.Cu), AGND (In1.Cu), +3V3_DIG (In2/In4.Cu), and +3V3_ANA (In2/In4.Cu).
    Returns (vias_added, list of (net, ref, pin) that remain outside plane polygons).
    """
    n_layers = board.GetCopperLayerCount()
    pwr_layer = pcbnew.In2_Cu if n_layers == 4 else pcbnew.In4_Cu
    via_sig_mask = 15
    plane_nets = ("GND", "AGND", "+3V3_DIG", "+3V3_ANA")
    nets_by_name = board.GetNetsByName()

    # Map net_name -> (layer, priority, zone_polygon), all zones by layer, and keepouts
    plane_zones: dict[str, tuple[int, int, object]] = {}
    layer_zones: dict[int, list[tuple[str, int, object]]] = {pcbnew.In1_Cu: [], pwr_layer: []}
    rule_areas: dict[int, list] = {pcbnew.In1_Cu: [], pwr_layer: []}
    for z in board.Zones():
        if z.GetIsRuleArea():
            if z.GetDoNotAllowZoneFills():
                for layer in (pcbnew.In1_Cu, pwr_layer):
                    if z.IsOnLayer(layer):
                        rule_areas[layer].append(z.Outline())
        else:
            nn = z.GetNetname().lstrip("/")
            prio = z.GetAssignedPriority()
            for layer in (pcbnew.In1_Cu, pwr_layer):
                if z.IsOnLayer(layer):
                    layer_zones[layer].append((nn, prio, z.Outline()))
                    if nn in plane_nets:
                        plane_zones[nn] = (layer, prio, z.Outline())

    # Collect obstacles: pads (non-PTH per F/B, PTH on both), drills, tracks/arcs, vias
    pad_obs = []   # (nn, layer_mask, x0, y0, x1, y1, cx, cy, is_circ, r)
    drill_obs = [] # (nn, cx, cy, rx, ry)
    hv_nets = {"CH1_TIP", "CH1_HV_MID", "CH2_TIP", "CH2_HV_MID"}
    plane_pads: dict[str, list[tuple[str, str, int, float, float]]] = {n: [] for n in plane_nets}

    pth_boxes_by_net: dict[tuple[str, str], list[tuple[float, float, float, float]]] = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            nn = p.GetNetname().lstrip("/")
            if nn.startswith("unconnected-"):
                nn = ""
            bb = p.GetBoundingBox()
            x0, y0 = pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY
            x1, y1 = pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY
            cx, cy = pcbnew.ToMM(p.GetPosition().x) - OX, pcbnew.ToMM(p.GetPosition().y) - OY
            ds = p.GetDrillSize()
            has_drill = ds.x > 0 or ds.y > 0
            mask = (1 if p.IsOnLayer(pcbnew.F_Cu) else 0) | (2 if p.IsOnLayer(pcbnew.B_Cu) else 0)
            is_circ = p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE
            r = 0.5 * (x1 - x0) if is_circ else 0.0
            if mask:
                pad_obs.append((nn, mask, x0, y0, x1, y1, cx, cy, is_circ, r))
            if has_drill:
                drill_obs.append((nn, cx, cy, pcbnew.ToMM(ds.x) / 2.0, pcbnew.ToMM(ds.y) / 2.0))
                if nn in plane_nets:
                    pth_boxes_by_net.setdefault((ref, nn), []).append((x0, y0, x1, y1))

    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref == "J5":
            continue
        for p in fp.Pads():
            nn = p.GetNetname().lstrip("/")
            if nn in plane_nets and p.GetDrillSize().x == 0 and p.IsOnCopperLayer():
                bb = p.GetBoundingBox()
                x0, y0 = pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY
                x1, y1 = pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY
                # Skip SMD EP/pad that directly overlaps a same-net PTH on the same footprint (e.g. U1.81)
                if any(x0 <= px1 and px0 <= x1 and y0 <= py1 and py0 <= y1 for px0, py0, px1, py1 in pth_boxes_by_net.get((ref, nn), ())):
                    continue
                cx, cy = pcbnew.ToMM(p.GetPosition().x) - OX, pcbnew.ToMM(p.GetPosition().y) - OY
                li = 1 if (p.IsOnLayer(pcbnew.B_Cu) and not p.IsOnLayer(pcbnew.F_Cu)) else 0
                plane_pads[nn].append((ref, p.GetNumber(), li, cx, cy))

    # Tracks/arcs and vias already emitted by routes.py
    seg_obs = []  # (nn, li, ax, ay, bx, by, hw)
    via_obs = []  # (nn, vx, vy, r)
    net_segs: dict[str, list[tuple[float, float, float, float]]] = {n: [] for n in plane_nets}
    net_vias: dict[str, list[tuple[float, float]]] = {n: [] for n in plane_nets}

    for tr in board.GetTracks():
        nn = tr.GetNetname().lstrip("/")
        if tr.GetClass() == "PCB_VIA":
            vx, vy = pcbnew.ToMM(tr.GetPosition().x) - OX, pcbnew.ToMM(tr.GetPosition().y) - OY
            vr = pcbnew.ToMM(tr.GetWidth(pcbnew.F_Cu)) / 2.0
            vdr = pcbnew.ToMM(tr.GetDrill()) / 2.0
            via_obs.append((nn, vx, vy, vr))
            drill_obs.append((nn, vx, vy, vdr, vdr))
            if nn in net_vias:
                net_vias[nn].append((vx, vy))
        elif tr.GetClass() in ("PCB_TRACK", "PCB_ARC"):
            layer = tr.GetLayer()
            sig_map = (
                {pcbnew.F_Cu: 0, pcbnew.B_Cu: 1, pcbnew.In2_Cu: 2, pcbnew.In1_Cu: 3}
                if n_layers == 4
                else {pcbnew.F_Cu: 0, pcbnew.B_Cu: 1, pcbnew.In2_Cu: 2, pcbnew.In3_Cu: 3}
            )
            li = sig_map.get(layer, -1)
            ax, ay = pcbnew.ToMM(tr.GetStart().x) - OX, pcbnew.ToMM(tr.GetStart().y) - OY
            bx, by = pcbnew.ToMM(tr.GetEnd().x) - OX, pcbnew.ToMM(tr.GetEnd().y) - OY
            hw = pcbnew.ToMM(tr.GetWidth()) / 2.0
            if li >= 0:
                seg_obs.append((nn, li, ax, ay, bx, by, hw))
            if nn in net_segs and li in (0, 1, 2, 3):
                net_segs[nn].append((ax, ay, bx, by))

    def in_plane_poly(nn: str, vx: float, vy: float, inset: float = 0.28) -> bool:
        if nn not in plane_zones:
            return False
        layer, my_prio, outline = plane_zones[nn]
        ring = [(0.0, 0.0)] + [
            (inset * math.cos(a * math.pi / 4), inset * math.sin(a * math.pi / 4)) for a in range(8)
        ]
        hi_inset = max(inset, 0.62)
        hi_ring = [(0.0, 0.0)] + [
            (hi_inset * math.cos(a * math.pi / 4), hi_inset * math.sin(a * math.pi / 4)) for a in range(8)
        ]
        # Higher-priority foreign zones carve out lower-priority planes (e.g., AGND/+3V3_ANA inside GND/+3V3_DIG)
        hi_outs = [zo for znet, zprio, zo in layer_zones[layer] if znet != nn and zprio > my_prio]
        for dx, dy in ring:
            q = pcbnew.VECTOR2I(MM(OX + vx + dx), MM(OY + vy + dy))
            if not outline.Contains(q):
                return False
            for ra in rule_areas[layer]:
                if ra.Contains(q):
                    return False
        for dx, dy in hi_ring:
            q = pcbnew.VECTOR2I(MM(OX + vx + dx), MM(OY + vy + dy))
            for ho in hi_outs:
                if ho.Contains(q):
                    return False
        return True

    def is_pad_grounded_via_routes(nn: str, cx: float, cy: float) -> bool:
        """Check whether (cx, cy) already reaches any via or PTH inside this plane polygon."""
        anchors = list(net_vias[nn])
        for onn, dcx, dcy, _, _ in drill_obs:
            if onn == nn and in_plane_poly(nn, dcx, dcy, inset=0.10):
                anchors.append((dcx, dcy))
        if not anchors:
            return False
        pts = [(cx, cy)]
        for ax, ay in anchors:
            if math.hypot(ax - cx, ay - cy) < 0.05:
                return True
        segs = net_segs[nn]
        visited = {0}
        # Collect all segment endpoints + pads on this net, BFS via segment adjacency
        nodes = [(cx, cy)] + [(ax, ay) for ax, ay in anchors]
        anchor_start, anchor_end = 1, 1 + len(anchors)
        for ax, ay, bx, by in segs:
            nodes.append((ax, ay))
            nodes.append((bx, by))
        queue = [0]
        while queue:
            ui = queue.pop(0)
            ux, uy = nodes[ui]
            if anchor_start <= ui < anchor_end:
                return True
            for ax, ay, bx, by in segs:
                if _seg_point_dist(ux, uy, ax, ay, bx, by) < 0.08:
                    for wi in range(len(nodes)):
                        if wi not in visited:
                            wx, wy = nodes[wi]
                            if _seg_point_dist(wx, wy, ax, ay, bx, by) < 0.08:
                                visited.add(wi)
                                queue.append(wi)
        return False

    def point_clear(nn: str, li_mask: int, x: float, y: float, half_r: float, check_drill: bool = True, is_via: bool = False) -> bool:
        if not _inside_board(x, y, 0.34 + half_r):
            return False
        base_clr = 0.45 if nn in hv_nets else 0.13
        drill_margin = (vdr + 0.275) if is_via else 0.0
        for onn, omask, x0, y0, x1, y1, cx, cy, is_circ, r in pad_obs:
            if onn == nn or not (omask & li_mask):
                continue
            req = max(base_clr + half_r, (0.45 + half_r) if onn in hv_nets else 0.0, drill_margin)
            if is_circ:
                if math.hypot(x - cx, y - cy) < r + req:
                    return False
            elif _box_point_dist(x, y, x0, y0, x1, y1) < req:
                return False
        for onn, sli, ax, ay, bx, by, ohw in seg_obs:
            if onn == nn or not (li_mask & (1 << sli)):
                continue
            req = max(base_clr + half_r + ohw, (0.45 + half_r + ohw) if onn in hv_nets else 0.0, drill_margin + ohw)
            if _seg_point_dist(x, y, ax, ay, bx, by) < req:
                return False
        for onn, vx, vy, vr_o in via_obs:
            if is_via:
                req = 0.72
            elif onn == nn:
                continue
            else:
                req = max(base_clr + half_r + vr_o, (0.45 + half_r + vr_o) if onn in hv_nets else 0.0, drill_margin + vr_o)
            if math.hypot(x - vx, y - vy) < req:
                return False
        if check_drill:
            for onn, dx, dy, rx, ry in drill_obs:
                if is_via:
                    hreq = max(rx, ry) + vdr + 0.265
                    if _box_point_dist(x, y, dx - rx, dy - ry, dx + rx, dy + ry) < vdr + 0.265:
                        return False
                elif onn != nn:
                    if _box_point_dist(x, y, dx - rx, dy - ry, dx + rx, dy + ry) < 0.27 + half_r:
                        return False
        return True

    def seg_clear(nn: str, li: int, ax: float, ay: float, bx: float, by: float, hw: float) -> bool:
        length = math.hypot(bx - ax, by - ay)
        steps = max(2, int(math.ceil(length / 0.08)) + 1)
        for k in range(steps):
            t = k / (steps - 1)
            if not point_clear(nn, 1 << li, ax + (bx - ax) * t, ay + (by - ay) * t, hw, check_drill=True):
                return False
        return True

    def in_smd_pad(x: float, y: float) -> bool:
        """True when a via at (x, y) would overlap any surface-mount pad (no via-in-pad, any net)."""
        m = vr + 0.05
        for _onn, omask, x0, y0, x1, y1, _cx, _cy, _circ, _r in pad_obs:
            if omask != 3 and x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m:
                return True
        return False

    added_vias = 0
    unplaced: list[tuple[str, str, str]] = []
    vr = DEFAULT_VIA[0] / 2.0
    vdr = DEFAULT_VIA[1] / 2.0
    stub_w = 0.10

    # 16 compass angles: cardinal, 45 deg diagonals, and 22.5 deg offsets
    dirs = [(math.cos(a), math.sin(a)) for a in (0, math.pi, -math.pi/2, math.pi/2, math.pi/4, -math.pi/4, 3*math.pi/4, -3*math.pi/4)]
    dirs += [(math.cos(a), math.sin(a)) for a in (k * math.pi / 8 for k in range(1, 16, 2))]
    radii = (0.0, 0.40, 0.52, 0.65, 0.80, 0.96, 1.15, 1.38, 1.65, 2.00)

    def bfs_connect_or_drop(
        nn: str,
        li: int,
        cx: float,
        cy: float,
        local_vias: list[tuple[float, float]],
        grounded_list: list[tuple[float, float]],
        step: float = 0.10,
        win: float = 16.0,
        lim: int = 20000,
    ) -> tuple[list[tuple[float, float]] | None, tuple[float, float] | None]:
        """Walk F.Cu/B.Cu on a fine grid around (cx, cy) to a shared via/PTH/grounded pad or new valid drop via."""
        import heapq
        hw = stub_w / 2.0
        gx0 = max(0.5, round((cx - win) / step) * step)
        gy0 = max(-8.4, round((cy - win) / step) * step)
        gx1 = min(137.5, round((cx + win) / step) * step)
        gy1 = min(8.4, round((cy + win) / step) * step)

        # Slice obstacles to the local window (+2 mm margin)
        m = 2.0
        li_bit = 1 << li
        loc_pads_sig = [p for p in pad_obs if p[0] != nn and (p[1] & li_bit) and p[4] >= gx0 - m and p[2] <= gx1 + m and p[5] >= gy0 - m and p[3] <= gy1 + m]
        loc_pads_all = [p for p in pad_obs if p[0] != nn and p[4] >= gx0 - m and p[2] <= gx1 + m and p[5] >= gy0 - m and p[3] <= gy1 + m]
        loc_segs_sig = [s for s in seg_obs if s[0] != nn and s[1] == li and max(s[2], s[4]) >= gx0 - m and min(s[2], s[4]) <= gx1 + m and max(s[3], s[5]) >= gy0 - m and min(s[3], s[5]) <= gy1 + m]
        loc_segs_all = [s for s in seg_obs if s[0] != nn and max(s[2], s[4]) >= gx0 - m and min(s[2], s[4]) <= gx1 + m and max(s[3], s[5]) >= gy0 - m and min(s[3], s[5]) <= gy1 + m]
        loc_vias_all = [v for v in via_obs if gx0 - m <= v[1] <= gx1 + m and gy0 - m <= v[2] <= gy1 + m]
        loc_vias_for = [v for v in loc_vias_all if v[0] != nn]
        loc_drills_all = [d for d in drill_obs if gx0 - m <= d[1] <= gx1 + m and gy0 - m <= d[2] <= gy1 + m]
        loc_drills_for = [d for d in loc_drills_all if d[0] != nn]

        def loc_trk_ok(x: float, y: float) -> bool:
            if not _inside_board(x, y, 0.32 + hw):
                return False
            base_clr = 0.45 if nn in hv_nets else 0.105
            for onn, _omask, x0, y0, x1, y1, pcx, pcy, is_circ, r in loc_pads_sig:
                req = max(base_clr + hw, (0.45 + hw) if onn in hv_nets else 0.0)
                if is_circ:
                    if math.hypot(x - pcx, y - pcy) < r + req:
                        return False
                elif _box_point_dist(x, y, x0, y0, x1, y1) < req:
                    return False
            for onn, _sli, ax, ay, bx, by, ohw in loc_segs_sig:
                req = max(base_clr + hw + ohw, (0.45 + hw + ohw) if onn in hv_nets else 0.0)
                if _seg_point_dist(x, y, ax, ay, bx, by) < req:
                    return False
            for onn, vx, vy, vr_o in loc_vias_for:
                req = max(base_clr + hw + vr_o, (0.45 + hw + vr_o) if onn in hv_nets else 0.0)
                if math.hypot(x - vx, y - vy) < req:
                    return False
            for _onn, dx, dy, rx, ry in loc_drills_for:
                if _box_point_dist(x, y, dx - rx, dy - ry, dx + rx, dy + ry) < 0.26 + hw:
                    return False
            return True

        def loc_seg_ok(ax: float, ay: float, bx: float, by: float) -> bool:
            length = math.hypot(bx - ax, by - ay)
            steps = max(2, int(math.ceil(length / 0.05)) + 1)
            for k in range(steps):
                t = k / (steps - 1)
                if not loc_trk_ok(ax + (bx - ax) * t, ay + (by - ay) * t):
                    return False
            return True

        def via_drop_ok(vx: float, vy: float) -> bool:
            if not in_plane_poly(nn, vx, vy, inset=0.25):
                return False
            if not _inside_board(vx, vy, 0.32 + vr):
                return False
            if any(math.hypot(vx - ox, vy - oy) < 0.72 for _, ox, oy, _ in loc_vias_all):
                return False
            drill_margin = vdr + 0.26
            base_clr = 0.45 if nn in hv_nets else 0.11
            half_r = vr + 0.005
            for onn, _omask, x0, y0, x1, y1, pcx, pcy, is_circ, r in loc_pads_all:
                req = max(base_clr + half_r, (0.45 + half_r) if onn in hv_nets else 0.0, drill_margin)
                if is_circ:
                    if math.hypot(vx - pcx, vy - pcy) < r + req:
                        return False
                elif _box_point_dist(vx, vy, x0, y0, x1, y1) < req:
                    return False
            for onn, _sli, ax, ay, bx, by, ohw in loc_segs_all:
                req = max(base_clr + half_r + ohw, (0.45 + half_r + ohw) if onn in hv_nets else 0.0, drill_margin + ohw)
                if _seg_point_dist(vx, vy, ax, ay, bx, by) < req:
                    return False
            for _onn, dx, dy, rx, ry in loc_drills_all:
                if _box_point_dist(vx, vy, dx - rx, dy - ry, dx + rx, dy + ry) < vdr + 0.265:
                    return False
            return True

        anchors_raw = [(vx, vy) for vx, vy in local_vias if gx0 <= vx <= gx1 and gy0 <= vy <= gy1 and in_plane_poly(nn, vx, vy, inset=0.10)]
        for onn, dcx, dcy, _, _ in loc_drills_all:
            if onn == nn and in_plane_poly(nn, dcx, dcy, inset=0.10):
                anchors_raw.append((dcx, dcy))
        for px_a, py_a in grounded_list:
            if gx0 <= px_a <= gx1 and gy0 <= py_a <= gy1 and math.hypot(px_a - cx, py_a - cy) > 0.05:
                anchors_raw.append((px_a, py_a))
        seen_a = set()
        anchors = []
        for ax, ay in anchors_raw:
            ak = (round(ax / 0.05) * 0.05, round(ay / 0.05) * 0.05)
            if ak not in seen_a:
                seen_a.add(ak)
                anchors.append((ax, ay))

        sx, sy = round(cx / step) * step, round(cy / step) * step
        prev: dict[tuple[int, int], tuple[int, int] | None] = {(0, 0): None}
        dist_g: dict[tuple[int, int], float] = {(0, 0): 0.0}
        pq: list[tuple[float, int, int]] = [(0.0, 0, 0)]
        visited = set()
        while pq and len(visited) < lim:
            g, ix, iy = heapq.heappop(pq)
            if (ix, iy) in visited:
                continue
            visited.add((ix, iy))
            px, py = (cx, cy) if (ix == 0 and iy == 0) else (round(sx + ix * step, 3), round(sy + iy * step, 3))
            if ix != 0 or iy != 0:
                for ax, ay in anchors:
                    if abs(ax - px) <= 0.45 and abs(ay - py) <= 0.45 and loc_seg_ok(px, py, ax, ay):
                        path = [(ax, ay)]
                        cur: tuple[int, int] | None = (ix, iy)
                        while cur is not None:
                            cpx, cpy = (cx, cy) if cur == (0, 0) else (round(sx + cur[0] * step, 3), round(sy + cur[1] * step, 3))
                            path.append((cpx, cpy))
                            cur = prev[cur]
                        path.reverse()
                        return path, None
                if via_drop_ok(px, py) and not in_smd_pad(px, py) and (math.hypot(px - cx, py - cy) > 0.20 or loc_seg_ok(cx, cy, px, py)):
                    path = []
                    cur = (ix, iy)
                    while cur is not None:
                        cpx, cpy = (cx, cy) if cur == (0, 0) else (round(sx + cur[0] * step, 3), round(sy + cur[1] * step, 3))
                        path.append((cpx, cpy))
                        cur = prev[cur]
                    path.reverse()
                    return path, (px, py)

            for dx_i, dy_i in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx_i, ny_i = ix + dx_i, iy + dy_i
                if (nx_i, ny_i) in visited:
                    continue
                nx, ny = round(sx + nx_i * step, 3), round(sy + ny_i * step, 3)
                if not (gx0 <= nx <= gx1 and gy0 <= ny <= gy1):
                    continue
                if not loc_trk_ok(nx, ny):
                    continue
                ng = g + 1.0
                if ng < dist_g.get((nx_i, ny_i), 1e18):
                    dist_g[(nx_i, ny_i)] = ng
                    prev[(nx_i, ny_i)] = (ix, iy)
                    heapq.heappush(pq, (ng, nx_i, ny_i))
        return None, None

    def _emit_path_and_via(nn: str, li: int, path: list[tuple[float, float]], new_via: tuple[float, float] | None, local_vias: list[tuple[float, float]]) -> bool:
        net_item = nets_by_name[NET(nn)]
        lay_enum = LAYERS["B"] if li == 1 else LAYERS["F"]
        # Simplify collinear points
        simp = [path[0]]
        for p in path[1:]:
            if len(simp) >= 2:
                ax, ay = simp[-2]
                bx, by = simp[-1]
                dx1, dy1 = bx - ax, by - ay
                dx2, dy2 = p[0] - bx, p[1] - by
                if abs(dx1 * dy2 - dy1 * dx2) < 1e-4 and (dx1 * dx2 + dy1 * dy2) >= 0:
                    simp[-1] = p
                    continue
            if math.hypot(p[0] - simp[-1][0], p[1] - simp[-1][1]) > 0.005:
                simp.append(p)
        for a, b in zip(simp, simp[1:]):
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(pcbnew.VECTOR2I(MM(OX + a[0]), MM(OY + a[1])))
            tr.SetEnd(pcbnew.VECTOR2I(MM(OX + b[0]), MM(OY + b[1])))
            tr.SetWidth(MM(stub_w))
            tr.SetLayer(lay_enum)
            tr.SetNet(net_item)
            board.Add(tr)
            seg_obs.append((nn, li, a[0], a[1], b[0], b[1], stub_w / 2.0))
            if nn in net_segs:
                net_segs[nn].append((a[0], a[1], b[0], b[1]))
        if new_via is not None:
            vx, vy = new_via
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(MM(OX + vx), MM(OY + vy)))
            via.SetWidth(MM(DEFAULT_VIA[0]))
            via.SetDrill(MM(DEFAULT_VIA[1]))
            via.SetNet(net_item)
            board.Add(via)
            via_obs.append((nn, vx, vy, vr))
            drill_obs.append((nn, vx, vy, vdr, vdr))
            local_vias.append((vx, vy))
            return True
        return False

    # Phase 1: run Step 1 (share <= 2.50 mm) and Step 2 (radial drop) across all 4 plane nets first
    local_vias_by_net: dict[str, list[tuple[float, float]]] = {nn: list(net_vias[nn]) for nn in plane_nets}
    grounded_pads: dict[tuple[str, int], list[tuple[float, float]]] = {(nn, li): [] for nn in plane_nets for li in (0, 1)}
    deferred_by_net: dict[str, list[tuple[str, str, int, float, float]]] = {nn: [] for nn in plane_nets}

    for nn in plane_nets:
        local_vias = local_vias_by_net[nn]
        for ref, pin, li, cx, cy in plane_pads[nn]:
            if is_pad_grounded_via_routes(nn, cx, cy):
                grounded_pads[(nn, li)].append((cx, cy))
                continue
            # 1) Try sharing an existing via of same net within 2.5 mm
            shared = None
            for vx, vy in sorted(local_vias, key=lambda p: math.hypot(p[0] - cx, p[1] - cy)):
                d = math.hypot(vx - cx, vy - cy)
                if d <= 2.50 and in_plane_poly(nn, vx, vy, inset=0.10) and seg_clear(nn, li, cx, cy, vx, vy, stub_w / 2.0):
                    shared = (vx, vy)
                    break
            if shared is not None:
                vx, vy = shared
                _emit_path_and_via(nn, li, [(cx, cy), (vx, vy)], None, local_vias)
                grounded_pads[(nn, li)].append((cx, cy))
                continue

            # 2) Search radial candidates for a clean new drop-via inside the target plane polygon
            chosen = None
            for rad in radii:
                if rad == 0.0:
                    continue                           # never a via in the pad itself
                cands = [(round(cx + dx * rad, 3), round(cy + dy * rad, 3)) for dx, dy in dirs]
                for vx, vy in cands:
                    if not in_plane_poly(nn, vx, vy, inset=0.28):
                        continue
                    if in_smd_pad(vx, vy):
                        continue
                    # Via occupies all signal layers, drill cannot hit any drill, via-to-via >= 0.56 mm
                    if not point_clear(nn, via_sig_mask, vx, vy, vr + 0.01, check_drill=True, is_via=True):
                        continue
                    if any(math.hypot(vx - ox, vy - oy) < 0.72 for _, ox, oy, _ in via_obs):
                        continue
                    if rad == 0.0 or seg_clear(nn, li, cx, cy, vx, vy, stub_w / 2.0):
                        chosen = (vx, vy)
                        break
                if chosen is not None:
                    break

            if chosen is not None:
                vx, vy = chosen
                if _emit_path_and_via(nn, li, [(cx, cy), (vx, vy)], (vx, vy), local_vias):
                    added_vias += 1
                grounded_pads[(nn, li)].append((cx, cy))
            else:
                deferred_by_net[nn].append((ref, pin, li, cx, cy))

    # Phase 2: interleaved F.Cu/B.Cu BFS fallback across all 4 plane nets (0.10 mm then 0.05 mm)
    for step, win, lim in ((0.10, 14.0, 15000), (0.10, 18.0, 20000), (0.05, 8.0, 25000), (0.05, 12.0, 30000)):
        if not any(deferred_by_net.values()):
            break
        for nn in plane_nets:
            deferred = deferred_by_net[nn]
            if not deferred:
                continue
            local_vias = local_vias_by_net[nn]
            next_deferred = []
            for ref, pin, li, cx, cy in deferred:
                if is_pad_grounded_via_routes(nn, cx, cy):
                    grounded_pads[(nn, li)].append((cx, cy))
                    continue
                path, new_via = bfs_connect_or_drop(
                    nn, li, cx, cy, local_vias, grounded_pads[(nn, li)],
                    step=step, win=win, lim=lim,
                )
                if path is not None:
                    if _emit_path_and_via(nn, li, path, new_via, local_vias):
                        added_vias += 1
                    grounded_pads[(nn, li)].append((cx, cy))
                else:
                    next_deferred.append((ref, pin, li, cx, cy))
            deferred_by_net[nn] = next_deferred

    for nn in plane_nets:
        for ref, pin, _li, _cx, _cy in deferred_by_net[nn]:
            unplaced.append((nn, ref, pin))

    return added_vias, unplaced


def add_frozen(board: pcbnew.BOARD, frozen):
    """Draw the frozen tracks, arcs and vias of hw/routes.py exactly as listed."""
    nets = board.GetNetsByName()

    def pt(x, y):
        return pcbnew.VECTOR2I(MM(OX + x), MM(OY + y))

    for net, layer, w, x1, y1, x2, y2 in frozen.SEGMENTS:
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pt(x1, y1))
        t.SetEnd(pt(x2, y2))
        t.SetWidth(MM(w))
        t.SetLayer(LAYERS[layer])
        t.SetNet(nets[NET(net)])
        board.Add(t)
    for net, layer, w, x1, y1, xm, ym, x2, y2 in frozen.ARCS:
        t = pcbnew.PCB_ARC(board)
        t.SetStart(pt(x1, y1))
        t.SetMid(pt(xm, ym))
        t.SetEnd(pt(x2, y2))
        t.SetWidth(MM(w))
        t.SetLayer(LAYERS[layer])
        t.SetNet(nets[NET(net)])
        board.Add(t)
    for net, x, y, size, drill in frozen.VIAS:
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt(x, y))
        v.SetWidth(MM(size))
        v.SetDrill(MM(drill))
        v.SetNet(nets[NET(net)])
        board.Add(v)


def freeze(board: pcbnew.BOARD, dst: Path):
    """Write every track, arc and via of the board to hw/routes.py."""
    names = {v: k for k, v in LAYERS.items()}
    segs, arcs, vias = [], [], []

    def xy(v):
        return round(pcbnew.ToMM(v.x) - OX, 4), round(pcbnew.ToMM(v.y) - OY, 4)

    for tr in board.GetTracks():
        net = tr.GetNetname().lstrip("/")
        cls = tr.GetClass()
        if cls == "PCB_VIA":
            vias.append((net, *xy(tr.GetPosition()), round(pcbnew.ToMM(tr.GetWidth(pcbnew.F_Cu)), 3), round(pcbnew.ToMM(tr.GetDrill()), 3)))
        elif cls == "PCB_ARC":
            # a very short arc is written as a straight segment: dropping it would break the connection
            if tr.GetLength() < MM(0.08):
                if tr.GetLength() >= MM(0.001):
                    segs.append((net, names[tr.GetLayer()], round(pcbnew.ToMM(tr.GetWidth()), 3), *xy(tr.GetStart()), *xy(tr.GetEnd())))
                continue
            arcs.append((net, names[tr.GetLayer()], round(pcbnew.ToMM(tr.GetWidth()), 3), *xy(tr.GetStart()), *xy(tr.GetMid()), *xy(tr.GetEnd())))
        elif tr.GetLength() >= MM(0.001):           # only zero-length leftovers are dropped
            segs.append((net, names[tr.GetLayer()], round(pcbnew.ToMM(tr.GetWidth()), 3), *xy(tr.GetStart()), *xy(tr.GetEnd())))
    out = [FROZEN_HEADER, "SEGMENTS = ["]
    out += [f"    {s!r}," for s in sorted(segs)]
    out += ["]", "", "ARCS = ["] + [f"    {a!r}," for a in sorted(arcs)]
    out += ["]", "", "VIAS = ["] + [f"    {v!r}," for v in sorted(vias)] + ["]", ""]
    dst.write_text("\n".join(out), encoding="utf8", newline="\n")
    print(f"froze {len(segs)} segments, {len(arcs)} arcs, {len(vias)} vias -> {dst}")


FROZEN_HEADER = '''"""Every track, arc and via of the routed ProbePen-AI board, drawn exactly as listed by hw/route.py.

Coordinates are board-datum mm (origin nose-tip centre, +X toward the USB-C tail, KiCad Y-down).
SEGMENTS: (net, layer, width, x1, y1, x2, y2)      ARCS: (net, layer, width, x1, y1, xm, ym, x2, y2)
VIAS: (net, x, y, pad, drill)

How this list was made (re-run after changing the circuit or the placement):
  1. hw/tools/plan_routes.py plans the nets whose geometry matters into hw/routes_critical.py.
  2. hw/route.py --tracks-only draws and locks those.
  3. Freerouting routes the remaining nets on F.Cu / B.Cu (hw/tools/freeroute_io.py).
  4. hw/route.py --finish adds any missing plane vias and fills the zones.
  5. hw/route.py --freeze writes the result here.
See hw/tools/groute/run.sh and docs/ROUTING_GUIDE.md.
"""
'''


def main():
    if "--tracks-only" in sys.argv:                    # planned critical tracks, locked; no vias, no fill
        from hw import escapes, rails, regulator_ck, routes_critical
        from hw.gen_pcb import DATUM
        board = pcbnew.LoadBoard(str(PCB_FILE))
        ux, uy = DATUM["rp2354b_xy"]

        class _Tpl:                                    # the copied regulator copper, at U1's position
            SEGMENTS = [(n, lay, w, ux + x1, uy + y1, ux + x2, uy + y2) for n, lay, w, x1, y1, x2, y2 in regulator_ck.TRACKS] + rails.segments() + list(escapes.TRACKS)
            ARCS = []
            VIAS = [(n, ux + x, uy + y, DEFAULT_VIA[0], DEFAULT_VIA[1]) for n, x, y in regulator_ck.VIAS] + \
                   [(n, x, y, DEFAULT_VIA[0], DEFAULT_VIA[1]) for n, x, y in rails.TIE_VIAS] + \
                   [(n, x, y, DEFAULT_VIA[0], DEFAULT_VIA[1]) for n, x, y in escapes.VIAS]

        add_frozen(board, _Tpl)
        add_routes(board, routes_critical.ROUTES)
        for tr in board.GetTracks():
            tr.SetLocked(True)
        pcbnew.SaveBoard(str(PCB_FILE), board)
        print("drew planned tracks on", PCB_FILE)
        return
    if "--finish" in sys.argv or "--freeze" in sys.argv:
        board = pcbnew.LoadBoard(str(PCB_FILE))
        if "--finish" in sys.argv:                     # plane and rail vias are planned (hw/tools/plan_routes.py)
            board.BuildConnectivity()
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
            pcbnew.SaveBoard(str(PCB_FILE), board)
        if "--freeze" in sys.argv:
            freeze(board, HERE / "routes.py")
        return
    from hw import routes

    board = pcbnew.LoadBoard(str(PCB_FILE))
    add_frozen(board, routes)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(PCB_FILE), board)
    print("routed and filled", PCB_FILE)


if __name__ == "__main__":
    main()
