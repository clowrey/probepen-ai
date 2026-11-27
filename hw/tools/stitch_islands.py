"""Stitch stranded outer-layer ground pour pieces to the inner ground planes.

After the zones are filled, every piece of a GND / AGND pour on F.Cu or B.Cu that holds no via of its net gets one,
at a point that is
  - inside the piece, at least 0.22 mm from its edge (the pour already keeps its clearance to other nets, so the
    via pad and drill then keep theirs on that layer),
  - over a filled zone of the same net on an inner layer (so the via actually ties the piece to a plane),
  - at least 0.35 mm (0.10 drill radius + 0.25 hole clearance) from every other net's copper on every other layer,
  - outside rule areas that forbid vias.

Run with KiCad's Python:
    kicad-python hw/tools/stitch_islands.py IN.kicad_pcb OUT.kicad_pcb
"""
import math
import sys

import pcbnew

NETS = ("/GND", "/AGND")
VIA, DRILL = 0.40, 0.25
EDGE_IN = 0.22
CLEAR = 0.35
MM, TO = pcbnew.FromMM, pcbnew.ToMM
OUTER = (pcbnew.F_Cu, pcbnew.B_Cu)
INNER = (pcbnew.In1_Cu, pcbnew.In2_Cu)


def seg_dist(px, py, ax, ay, bx, by):
    l2 = (bx - ax) ** 2 + (by - ay) ** 2
    k = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / l2))
    return math.hypot(px - ax - k * (bx - ax), py - ay - k * (by - ay))


def main(src, dst):
    b = pcbnew.LoadBoard(src)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    tracks = list(b.GetTracks())
    pads = [p for fp in b.GetFootprints() for p in fp.Pads()]
    no_via = [z for z in b.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    added = 0
    for zone in b.Zones():
        net = zone.GetNetname()
        if zone.GetIsRuleArea() or net not in NETS:
            continue
        for layer in OUTER:
            if not zone.IsOnLayer(layer):
                continue
            fill = zone.GetFilledPolysList(layer)
            for k in range(fill.OutlineCount()):
                piece = pcbnew.SHAPE_POLY_SET()
                piece.AddOutline(fill.Outline(k))
                if any(t.GetClass() == "PCB_VIA" and t.GetNetname() == net and piece.Contains(t.GetPosition())
                       for t in tracks):
                    continue
                if any(p.GetNetname() == net and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and piece.Contains(p.GetPosition())
                       for p in pads):
                    continue
                inner = pcbnew.SHAPE_POLY_SET(piece)
                inner.Deflate(MM(EDGE_IN), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MM(0.01))
                cands = []
                for o in range(inner.OutlineCount()):
                    line = inner.Outline(o)
                    cands += [line.CPoint(i) for i in range(line.PointCount())]
                site = None
                for c in cands:
                    cx, cy = TO(c.x), TO(c.y)
                    if any(z.Outline().Contains(c) for z in no_via):
                        continue
                    if not any(z.GetNetname() == net and not z.GetIsRuleArea() and z.IsOnLayer(il)
                               and z.GetFilledPolysList(il).Contains(c) for z in b.Zones() for il in INNER):
                        continue
                    ok = True
                    for t in tracks:
                        if t.GetNetname() == net:
                            continue
                        if t.GetClass() == "PCB_VIA":
                            q = t.GetPosition()
                            if math.hypot(TO(q.x) - cx, TO(q.y) - cy) < CLEAR + VIA / 2 + 0.05:
                                ok = False
                                break
                            continue
                        s, e = t.GetStart(), t.GetEnd()
                        if seg_dist(cx, cy, TO(s.x), TO(s.y), TO(e.x), TO(e.y)) < CLEAR + TO(t.GetWidth()) / 2:
                            ok = False
                            break
                    if not ok:
                        continue
                    for p in pads:
                        if p.GetNetname() == net:
                            continue
                        bb = p.GetBoundingBox()
                        dx = max(TO(bb.GetLeft()) - cx, 0, cx - TO(bb.GetRight()))
                        dy = max(TO(bb.GetTop()) - cy, 0, cy - TO(bb.GetBottom()))
                        if math.hypot(dx, dy) < CLEAR:
                            ok = False
                            break
                    if ok:
                        site = c
                        break
                if site is None:
                    print(f"no site: {net} piece on {b.GetLayerName(layer)} near "
                          f"({TO(piece.BBox().GetCenter().x) - 20:.1f}, {TO(piece.BBox().GetCenter().y) - 50:.1f})")
                    continue
                v = pcbnew.PCB_VIA(b)
                v.SetPosition(site)
                v.SetWidth(MM(VIA))
                v.SetDrill(MM(DRILL))
                v.SetNet(zone.GetNet())
                b.Add(v)
                tracks.append(v)
                added += 1
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(dst, b)
    print("stitched", added, "pour pieces")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
