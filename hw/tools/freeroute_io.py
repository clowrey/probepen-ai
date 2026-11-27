"""Specctra exchange with the Freerouting autorouter (https://github.com/freerouting/freerouting).

    kicad-python hw/tools/freeroute_io.py export hw/probepen_ai.kicad_pcb /tmp/pp_fr/board.dsn
    freerouting-cli -de /tmp/pp_fr/board.dsn -do /tmp/pp_fr/board.ses -mp 60
    kicad-python hw/tools/freeroute_io.py import hw/probepen_ai.kicad_pcb /tmp/pp_fr/board.ses

The export works on a copy of the board in which In1.Cu / In2.Cu are power planes (so nothing is
routed there) and the outer-layer pours are left out (they are filled after routing).
"""
import re
import sys

try:
    import pcbnew
except ImportError:                                    # `rules` runs under plain Python
    pcbnew = None

HV = ["/CH1_TIP", "/CH1_HV_MID", "/CH2_TIP", "/CH2_HV_MID"]
# class -> (track width, clearance) in um. 160 um keeps copper 0.25 mm from a 0.20 mm via hole.
RULES = {"kicad_default": (150, 160), "USB": (180, 160), "LV": (150, 160), "ANALOG": (180, 200),
         "HIGHSPEED": (130, 160), "POWER": (250, 160)}


def rules(dsn: str, out: str):
    """Rewrite the widths / clearances KiCad exported with the ones this board is routed to."""
    s = open(dsn).read()
    for cls, (w, c) in RULES.items():
        m = re.search(r"\(class %s .*?\(rule\s*\(width \d+\)\s*\(clearance \d+\)" % cls, s, re.S)
        blk = m.group(0)
        s = s.replace(blk, re.sub(r"\(width \d+\)\s*\(clearance \d+\)$", "(width %d)\n        (clearance %d)" % (w, c), blk))
    s = re.sub(r"\(rule\s*\(width \d+\)\s*\(clearance \d+\)\s*\(clearance \d+ \(type smd_smd\)\)",
               "(rule\n      (width 150)\n      (clearance 160)\n      (clearance 100 (type smd_smd))", s, count=1)
    old_via = re.search(r'\(via "([^"]+)"', s).group(1)
    via = "Via[0-3]_400:200_um"                        # smallest via the design rules allow
    blk = re.search(r'\(padstack "%s".*?\n    \)\n' % re.escape(old_via), s, re.S).group(0)
    dia = re.search(r"\(circle \S+ (\d+)\)", blk).group(1)
    s = s.replace(blk, blk.replace(old_via, via).replace("circle F.Cu %s" % dia, "circle F.Cu 400")
                  .replace("circle In1.Cu %s" % dia, "circle In1.Cu 400").replace("circle In2.Cu %s" % dia, "circle In2.Cu 400")
                  .replace("circle B.Cu %s" % dia, "circle B.Cu 400"))
    s = s.replace('"%s"' % old_via, '"%s"' % via)
    for n in HV:
        s = re.sub(r"(\(class ANALOG [^()]*?)%s\s" % re.escape(n), r"\1", s, count=1, flags=re.S)
    i = s.index("(class ANALOG")
    s = (s[:i] + "(class HV " + " ".join(HV) + '\n      (circuit\n        (use_via "%s")\n      )\n      (rule\n'
         "        (width 250)\n        (clearance 450)\n      )\n    )\n    " % via + s[i:])
    s = s.replace("(type route)", "(type protect)")   # tracks already on the board are not to be moved
    open(out, "w").write(s)
    print("rules written", out, "via", via)



def export(pcb: str, dsn: str):
    board = pcbnew.LoadBoard(pcb)
    # keep-off strips along the board edge so routed copper stays 0.3 mm inside it
    from hardware.pcb_datum import PCB, half_width_at
    from hw.gen_pcb import OX, OY
    length = float(PCB["length"])
    xs = [length * k / 276.0 for k in range(277)]
    strip = 0.22

    def area(pts):
        z = pcbnew.ZONE(board)
        ls = pcbnew.LSET()
        ls.AddLayer(pcbnew.F_Cu)
        ls.AddLayer(pcbnew.B_Cu)
        z.SetLayerSet(ls)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(True)
        z.SetDoNotAllowVias(True)
        z.SetDoNotAllowZoneFills(False)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        o = z.Outline()
        o.NewOutline()
        for x, y in pts:
            o.Append(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))
        board.Add(z)

    for sign in (-1, 1):
        area([(x, sign * half_width_at(x)) for x in xs] + [(x, sign * (half_width_at(x) - strip)) for x in reversed(xs)])
    hw0, hw1 = half_width_at(0.0), half_width_at(length)
    area([(0, -hw0), (strip, -hw0), (strip, hw0), (0, hw0)])
    area([(length - strip, -hw1), (length, -hw1), (length, hw1), (length - strip, hw1)])
    for layer in (pcbnew.In1_Cu, pcbnew.In2_Cu):
        board.SetLayerType(layer, pcbnew.LT_POWER)
    for z in list(board.Zones()):
        if not z.GetIsRuleArea() and (z.IsOnLayer(pcbnew.F_Cu) or z.IsOnLayer(pcbnew.B_Cu)):
            board.Remove(z)
    ok = pcbnew.ExportSpecctraDSN(board, dsn)
    print("exported" if ok else "EXPORT FAILED", dsn)


def import_(pcb: str, ses: str):
    board = pcbnew.LoadBoard(pcb)
    ok = pcbnew.ImportSpecctraSES(board, ses)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(pcb, board)
    print("imported" if ok else "IMPORT FAILED", ses, "tracks:", len(board.GetTracks()))


if __name__ == "__main__":
    {"export": export, "import": import_, "rules": rules}[sys.argv[1]](sys.argv[2], sys.argv[3])
