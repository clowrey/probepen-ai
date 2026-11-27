"""ko.py IN OUT add x0 y0 x1 y1 [x0 y0 x1 y1 ...] | ko.py IN OUT clear : router-only keep-out rectangles on User.2."""
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1]); OX, OY = 20.0, 50.0; MM = pcbnew.FromMM
if sys.argv[3] == "clear":
    for d in list(b.GetDrawings()):
        if d.GetLayer() == pcbnew.User_2:
            b.Remove(d)
else:
    v = list(map(float, sys.argv[4:]))
    for k in range(0, len(v), 4):
        x0, y0, x1, y1 = v[k:k + 4]
        s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_POLY); s.SetLayer(pcbnew.User_2); s.SetFilled(True)
        s.SetPolyPoints([pcbnew.VECTOR2I(MM(OX + x), MM(OY + y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
        b.Add(s)
pcbnew.SaveBoard(sys.argv[2], b)
