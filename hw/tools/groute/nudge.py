"""nudge.py IN OUT DRC_JSON : move supply / ground vias out of hole-clearance violations (with their tracks)."""
import json, math, sys, pcbnew
PLANE = {"/GND", "/AGND", "/+3V3_DIG", "/+3V3_ANA", "/+1V1_CORE", "/+3V0_REF", "/+5V_VBUS", "/VBUS_RAW", "/+5V_ANA_IN"}
HOLE = 0.25 + 0.012
b = pcbnew.LoadBoard(sys.argv[1]); d = json.load(open(sys.argv[3]))
byid = {}
for t in b.GetTracks():
    byid[t.m_Uuid.AsString()] = t
for fp in b.GetFootprints():
    for p in fp.Pads():
        byid[p.m_Uuid.AsString()] = p
mm = pcbnew.ToMM; MM = pcbnew.FromMM
moved = 0
for v in d["violations"]:
    if v["type"] != "hole_clearance":
        continue
    objs = [byid.get(i["uuid"]) for i in v["items"]]
    if None in objs:
        continue
    vias = [o for o in objs if isinstance(o, pcbnew.PCB_VIA) and o.GetNetname() in PLANE]
    if not vias:
        continue
    via = vias[0]; other = objs[1] if objs[0] is via else objs[0]
    c = via.GetPosition(); cx, cy = mm(c.x), mm(c.y)
    r = mm(via.GetDrillValue()) / 2
    if isinstance(other, pcbnew.PCB_VIA) or isinstance(other, pcbnew.PAD):
        o = other.GetPosition(); px, py = mm(o.x), mm(o.y)
        half = (mm(other.GetWidth(pcbnew.F_Cu)) if isinstance(other, pcbnew.PCB_VIA) else max(mm(other.GetSize().x), mm(other.GetSize().y))) / 2
    else:
        s, e = other.GetStart(), other.GetEnd()
        ax, ay, bx, by = mm(s.x), mm(s.y), mm(e.x), mm(e.y)
        L2 = (bx - ax) ** 2 + (by - ay) ** 2
        k = 0 if L2 == 0 else max(0, min(1, ((cx - ax) * (bx - ax) + (cy - ay) * (by - ay)) / L2))
        px, py = ax + k * (bx - ax), ay + k * (by - ay)
        half = mm(other.GetWidth()) / 2
    dist = math.hypot(cx - px, cy - py)
    need = half + HOLE + r
    if dist >= need or dist < 1e-6:
        continue
    ux, uy = (cx - px) / dist, (cy - py) / dist
    nx, ny = cx + ux * (need - dist), cy + uy * (need - dist)
    newp = pcbnew.VECTOR2I(MM(nx), MM(ny))
    for t in b.GetTracks():
        if t.GetNetCode() != via.GetNetCode() or isinstance(t, pcbnew.PCB_VIA):
            continue
        for get, set_ in ((t.GetStart, t.SetStart), (t.GetEnd, t.SetEnd)):
            q = get()
            if abs(mm(q.x) - cx) < 0.002 and abs(mm(q.y) - cy) < 0.002:
                set_(newp)
    via.SetPosition(newp); moved += 1
pcbnew.SaveBoard(sys.argv[2], b)
print("moved", moved, "vias")
