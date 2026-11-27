"""Dump the fixed obstacles of the routed board for hw/tools/tidy_routes.py (run with kicad-python).

Writes hw/out/tidy_obstacles.json: pads (per copper layer, as polygons), holes, rule areas that ban
tracks, fixed-net copper zones, and the board outline. Coordinates are board-datum mm.
"""
import json
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parents[1]
OX, OY = 20.0, 50.0
LAYERS = {pcbnew.F_Cu: "F", pcbnew.In1_Cu: "In1", pcbnew.In2_Cu: "In2", pcbnew.B_Cu: "B"}


def mm(v):
    return v / 1e6


def pt(p):
    return (round(mm(p.x) - OX, 4), round(mm(p.y) - OY, 4))


def outlines(polyset):
    out = []
    for i in range(polyset.OutlineCount()):
        o = polyset.Outline(i)
        out.append([pt(o.CPoint(k)) for k in range(o.PointCount())])
    return out


def net(name):
    return name[1:] if name.startswith("/") else name


board = pcbnew.LoadBoard(str(HERE / "probepen_ai.kicad_pcb"))
pads, holes = [], []
for fp in board.GetFootprints():
    for pad in fp.Pads():
        n = net(pad.GetNetname())
        for lid, lname in LAYERS.items():
            if not pad.IsOnLayer(lid):
                continue
            ps = pcbnew.SHAPE_POLY_SET()
            pad.TransformShapeToPolygon(ps, lid, 0, pcbnew.FromMM(0.002), pcbnew.ERROR_INSIDE)
            for poly in outlines(ps):
                pads.append({"net": n, "layer": lname, "poly": poly, "ref": fp.GetReference(), "pad": pad.GetNumber()})
        if pad.HasHole():
            d = pad.GetDrillSize()
            holes.append({"net": n, "xy": pt(pad.GetPosition()), "r": mm(max(d.x, d.y)) / 2,
                          "npth": pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH})

areas, zones = [], []
for z in board.Zones():
    lays = [ln for lid, ln in LAYERS.items() if z.IsOnLayer(lid)]
    if z.GetIsRuleArea():
        areas.append({"name": z.GetZoneName(), "layers": lays, "no_tracks": bool(z.GetDoNotAllowTracks()),
                      "no_vias": bool(z.GetDoNotAllowVias()), "polys": outlines(z.Outline())})
    else:
        zones.append({"name": z.GetZoneName(), "net": net(z.GetNetname()), "layers": lays,
                      "polys": outlines(z.Outline())})

edge = pcbnew.SHAPE_POLY_SET()
board.GetBoardPolygonOutlines(edge, False)
classes = {}
for name, ni in board.GetNetsByName().items():
    classes[net(str(name))] = ni.GetNetClassName()

out = {"pads": pads, "holes": holes, "areas": areas, "zones": zones, "edge": outlines(edge), "netclass": classes}
dst = HERE / "out" / "tidy_obstacles.json"
dst.write_text(json.dumps(out))
print(f"wrote {dst}: {len(pads)} pad shapes, {len(holes)} holes, {len(areas)} rule areas, {len(zones)} zones")
