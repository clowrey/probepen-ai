"""Dump every pad and unplated hole of a board to JSON for hw/tools/plan_routes.py.

Coordinates are board-datum mm with KiCad's Y-down sense (the same frame as PLACE in hw/gen_pcb.py).

Run with KiCad's Python:
    kicad-python hw/tools/dump_pads.py hw/probepen_ai.kicad_pcb out.json
"""
import json
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hw.gen_pcb import OX, OY  # noqa: E402


def main(src: str, dst: str):
    board = pcbnew.LoadBoard(src)
    pads, holes = [], []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            bb = p.GetBoundingBox()
            pos = p.GetPosition()
            ds = p.GetDrillSize()
            drill = max(pcbnew.ToMM(ds.x), pcbnew.ToMM(ds.y))
            on_f, on_b = p.IsOnLayer(pcbnew.F_Cu), p.IsOnLayer(pcbnew.B_Cu)
            x, y = pcbnew.ToMM(pos.x) - OX, pcbnew.ToMM(pos.y) - OY
            if drill and p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                holes.append({"ref": ref, "x": round(x, 4), "y": round(y, 4), "d": drill})
                continue
            net = p.GetNetname().lstrip("/")
            if net.startswith("unconnected-"):
                net = ""
            pads.append({
                "ref": ref, "num": p.GetNumber(), "net": net,
                "x": round(x, 4), "y": round(y, 4),
                "x0": round(pcbnew.ToMM(bb.GetLeft()) - OX, 4), "y0": round(pcbnew.ToMM(bb.GetTop()) - OY, 4),
                "x1": round(pcbnew.ToMM(bb.GetRight()) - OX, 4), "y1": round(pcbnew.ToMM(bb.GetBottom()) - OY, 4),
                "f": on_f, "b": on_b, "drill": drill,
            })
    Path(dst).write_text(json.dumps({"pads": pads, "holes": holes}), encoding="utf8")
    print(f"{len(pads)} pads, {len(holes)} unplated holes -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
