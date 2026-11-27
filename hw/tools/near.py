"""List board copper near a point (datum mm: x from the nose, y KiCad direction).

    kicad-python hw/tools/near.py X Y [R]
"""
import sys

import pcbnew

from hw.gen_pcb import OX, OY

x, y = float(sys.argv[1]), float(sys.argv[2])
r = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
b = pcbnew.LoadBoard("hw/probepen_ai.kicad_pcb")
TO = pcbnew.ToMM
box = pcbnew.BOX2I(pcbnew.VECTOR2I(pcbnew.FromMM(x + OX - r), pcbnew.FromMM(y + OY - r)),
                   pcbnew.VECTOR2L(pcbnew.FromMM(2 * r), pcbnew.FromMM(2 * r)))
for fp in b.GetFootprints():
    for p in fp.Pads():
        if p.GetBoundingBox().Intersects(box):
            q = p.GetPosition()
            s = p.GetSize()
            print(f"pad {fp.GetReference()}.{p.GetNumber():3s} {p.GetNetname():14s} ({TO(q.x)-OX:7.3f},{TO(q.y)-OY:7.3f}) "
                  f"{TO(s.x):.2f}x{TO(s.y):.2f} rot{p.GetOrientationDegrees():.0f} {'F' if p.IsOnLayer(pcbnew.F_Cu) else 'B'}")
for t in b.GetTracks():
    if t.GetBoundingBox().Intersects(box):
        if t.GetClass() == "PCB_VIA":
            q = t.GetPosition()
            print(f"via {t.GetNetname():14s} ({TO(q.x)-OX:7.3f},{TO(q.y)-OY:7.3f})")
        else:
            s, e = t.GetStart(), t.GetEnd()
            print(f"trk {t.GetNetname():14s} {b.GetLayerName(t.GetLayer()):5s} ({TO(s.x)-OX:7.3f},{TO(s.y)-OY:7.3f})->"
                  f"({TO(e.x)-OX:7.3f},{TO(e.y)-OY:7.3f}) w{TO(t.GetWidth()):.2f}")
