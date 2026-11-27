"""Write hw/out/fp_geometry.json: pads and fab outline of every footprint the design uses, in the footprint's
own frame (top side, unrotated), for hw/gen_3d.py to build models from.

    kicad-python hw/tools/dump_fp_geometry.py
"""
import json
from pathlib import Path

import pcbnew

from hw import design
from hw.gen_pcb import load_fp

OUT = Path(__file__).resolve().parents[2] / "hw" / "out" / "fp_geometry.json"
TO = pcbnew.ToMM


def main() -> None:
    out = {}
    for c in design.COMPONENTS:
        if not c.footprint or c.footprint in out:
            continue
        fp = load_fp(c.footprint)
        pads = []
        for p in fp.Pads():
            pos, size = p.GetPosition(), p.GetSize()
            pads.append({
                "num": p.GetNumber(), "x": round(TO(pos.x), 4), "y": round(TO(pos.y), 4),
                "w": round(TO(size.x), 4), "h": round(TO(size.y), 4),
                "rot": round(p.GetOrientationDegrees(), 2), "smd": p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD,
                "drill": round(TO(p.GetDrillSizeX()), 4),
            })
        fab = []
        for it in fp.GraphicalItems():
            if it.GetLayer() == pcbnew.F_Fab and isinstance(it, pcbnew.PCB_SHAPE):
                bb = it.GetBoundingBox()
                fab.append([round(TO(v), 4) for v in (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom())])
        out[c.footprint] = {"pads": pads, "fab": fab, "refs": [x.ref for x in design.COMPONENTS if x.footprint == c.footprint]}
    OUT.write_text(json.dumps(out, indent=1), encoding="utf8")
    print("wrote", OUT, len(out), "footprints")


if __name__ == "__main__":
    main()
