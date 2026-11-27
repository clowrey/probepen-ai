"""netswap.py IN OUT MODE: MODE 'hide' moves AGND copper and pads nose-side of x = 26.5 to net AGND_NOSE_TMP and puts
C41/C41B pad 2 on GND; MODE 'show' moves AGND_NOSE_TMP back to AGND."""
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1]); OX = 20.0; mode = sys.argv[3]
nets = b.GetNetsByName()
if mode == "hide":
    tmp = pcbnew.NETINFO_ITEM(b, "AGND_NOSE_TMP"); b.Add(tmp)
    src, dst = nets["/AGND"], tmp
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if fp.GetReference() in ("C41", "C41B") and p.GetNumber() == "2":
                p.SetNet(nets["/GND"])
else:
    src, dst = nets["AGND_NOSE_TMP"], nets["/AGND"]
n = 0
for fp in b.GetFootprints():
    for p in fp.Pads():
        if p.GetNetCode() == src.GetNetCode() and (mode == "show" or pcbnew.ToMM(p.GetPosition().x) - OX < 26.5):
            p.SetNet(dst); n += 1
for t in b.GetTracks():
    if t.GetNetCode() == src.GetNetCode():
        xs = [pcbnew.ToMM(t.GetStart().x) - OX, pcbnew.ToMM(t.GetEnd().x) - OX]
        if mode == "show" or max(xs) < 26.5:
            t.SetNet(dst); n += 1
pcbnew.SaveBoard(sys.argv[2], b); print(mode, n, "items")
