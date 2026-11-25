"""Dump footprints, pads, tracks, vias, zones and keepouts of a board to JSON.
Coordinates are datum CAD coordinates (mm, origin nose-tip center, +X tail, +Y up).

Run with KiCad's Python:
    kicad-python hw/tools/dump_board.py hw/probepen_ai.kicad_pcb out.json
"""
import json
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hw.gen_pcb import L, HW_Y, OX, OY  # noqa: E402


def to_cad(x_nm: int, y_nm: int) -> tuple[float, float]:
    """KiCad nm -> CAD datum mm (x in [0, 132], y_up in [-7.5, +7.5])."""
    return round(x_nm / 1e6 - OX, 4), round(-(y_nm / 1e6 - OY), 4)


def bbox(item) -> list[float]:
    b = item.GetBoundingBox()
    x0, y1 = to_cad(b.GetLeft(), b.GetTop())
    x1, y0 = to_cad(b.GetRight(), b.GetBottom())
    return [x0, y0, x1, y1]


def crtyd_bbox(fp: pcbnew.FOOTPRINT) -> list[float] | None:
    xs, ys = [], []
    for g in fp.GraphicalItems():
        if g.GetLayer() in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            b = g.GetBoundingBox()
            x0, y1 = to_cad(b.GetLeft(), b.GetTop())
            x1, y0 = to_cad(b.GetRight(), b.GetBottom())
            xs.extend([x0, x1])
            ys.extend([y0, y1])
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def main(pcb_path: str, out_path: str):
    board = pcbnew.LoadBoard(pcb_path)
    fps, copper, rule_areas = [], [], []

    for fp in board.GetFootprints():
        x, y = to_cad(fp.GetPosition().x, fp.GetPosition().y)
        drill = max((p.GetDrillSize().x / 1e6 for p in fp.Pads()), default=0.0)
        fps.append({
            "ref": fp.GetReference(),
            "value": fp.GetValue(),
            "fpid": str(fp.GetFPID().GetLibItemName()),
            "x": x,
            "y": y,
            "rot": fp.GetOrientationDegrees(),
            "layer": board.GetLayerName(fp.GetLayer()),
            "drill": round(drill, 4),
            "dnp": bool(fp.IsDNP()),
            "courtyard": crtyd_bbox(fp),
        })
        for p in fp.Pads():
            if p.IsOnCopperLayer():
                copper.append({
                    "kind": "pad",
                    "ref": fp.GetReference(),
                    "pad": p.GetNumber(),
                    "net": p.GetNetname(),
                    "netclass": p.GetNetClassName() if p.GetNetname() else "",
                    "layer": board.GetLayerName(fp.GetLayer()),
                    "bbox": bbox(p),
                })

    for t in board.GetTracks():
        cls = t.GetClass()
        item = {
            "kind": cls,
            "ref": "",
            "net": t.GetNetname(),
            "netclass": t.GetNetClassName(),
            "start": to_cad(t.GetStart().x, t.GetStart().y),
            "end": to_cad(t.GetEnd().x, t.GetEnd().y),
            "bbox": bbox(t),
            "layer": t.GetLayerName(),
            "width": (t.GetWidth(pcbnew.F_Cu) if cls == "PCB_VIA" else t.GetWidth()) / 1e6,
        }
        if cls == "PCB_ARC":
            item["mid"] = to_cad(t.GetMid().x, t.GetMid().y)
        copper.append(item)

    for z in board.Zones():
        if z.GetIsRuleArea():
            rb = bbox(z)
            rule_areas.append({
                "name": z.GetZoneName(),
                "layers": [board.GetLayerName(l) for l in z.GetLayerSet().CuStack()],
                "bbox": rb,
                "no_pour": bool(z.GetDoNotAllowZoneFills()),
            })
            continue

        zbx0, zby0, zbx1, zby1 = bbox(z)
        for layer in z.GetLayerSet().CuStack():
            polys = z.GetFilledPolysList(layer)
            pts = []
            x = max(0.0, round(zbx0 * 2) / 2)
            while x <= min(L, zbx1):
                y = max(-HW_Y, round(zby0 * 2) / 2)
                while y <= min(HW_Y, zby1):
                    if polys.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(x + OX), pcbnew.FromMM(OY - y))):
                        pts.append([x, y])
                    y += 0.5
                x += 0.5
            if pts:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                copper.append({
                    "kind": "zone",
                    "ref": "",
                    "net": z.GetNetname(),
                    "netclass": z.GetNetClassName(),
                    "layer": board.GetLayerName(layer),
                    "points": pts,
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                })

    Path(out_path).write_text(
        json.dumps({"footprints": fps, "copper": copper, "rule_areas": rule_areas}, indent=1),
        encoding="utf8",
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
