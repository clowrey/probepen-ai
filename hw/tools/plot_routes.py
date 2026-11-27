"""Plot one layer of hw/routes.py (or another routes file) in a window, with pads, for reviewing track shapes.

    .venv/bin/python hw/tools/plot_routes.py LAYER X0 X1 [ROUTES.py] [OUT.png] [px_per_mm]

Pads come from hw/out/tidy_obstacles.json (kicad-python hw/tools/tidy_extract.py).
"""
import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
lay, x0, x1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
src = Path(sys.argv[4]) if len(sys.argv) > 4 else ROOT / "hw/routes.py"
dst = sys.argv[5] if len(sys.argv) > 5 else f"/tmp/plot_{lay}_{x0:g}_{x1:g}.png"
S = float(sys.argv[6]) if len(sys.argv) > 6 else 40.0
y0, y1 = -9.0, 9.0
spec = importlib.util.spec_from_file_location("r", src)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
obst = json.loads((ROOT / "hw/out/tidy_obstacles.json").read_text())
W, H = int((x1 - x0) * S), int((y1 - y0) * S)
im = Image.new("RGB", (W, H), (16, 20, 28))
d = ImageDraw.Draw(im)


def P(x, y):
    return ((x - x0) * S, (y - y0) * S)


d.line([P(x0, -8.8), P(x1, -8.8)], fill=(90, 90, 90))
d.line([P(x0, 8.8), P(x1, 8.8)], fill=(90, 90, 90))
for p in obst["pads"]:
    if p["layer"] == lay:
        d.polygon([P(*q) for q in p["poly"]], fill=(150, 130, 60))
col = {"F": (220, 60, 60), "B": (80, 130, 220), "In1": (230, 140, 40), "In2": (90, 200, 120)}[lay]
for n, L, w, xa, ya, xb, yb in r.SEGMENTS:
    if L == lay:
        d.line([P(xa, ya), P(xb, yb)], fill=col, width=max(1, int(w * S)))
        for x, y in ((xa, ya), (xb, yb)):
            rr = w * S / 2
            d.ellipse([P(x, y)[0] - rr, P(x, y)[1] - rr, P(x, y)[0] + rr, P(x, y)[1] + rr], fill=col)
for n, L, w, xa, ya, xm, ym, xb, yb in r.ARCS:
    if L == lay:
        d.line([P(xa, ya), P(xm, ym), P(xb, yb)], fill=col, width=max(1, int(w * S)))
for n, x, y, size, drill in r.VIAS:
    cx, cy = P(x, y)
    rr = size * S / 2
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(200, 200, 200))
    rr = drill * S / 2
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(16, 20, 28))
im.save(dst)
print(dst, im.size)
