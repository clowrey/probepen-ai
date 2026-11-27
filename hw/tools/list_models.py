"""List every footprint on the board with its 3D model path and whether the file resolves.

    kicad-python hw/tools/list_models.py [board]
"""
import os
import re
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parents[2]
board_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "hw" / "probepen_ai.kicad_pcb")
b = pcbnew.LoadBoard(board_path)
env = dict(os.environ)
env.setdefault("KIPRJMOD", str(Path(board_path).parent))
stock = [v for k, v in env.items() if re.match(r"KICAD\d*_3DMODEL_DIR", k)]


def resolve(p: str) -> str | None:
    s = re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), p)
    cands = [s]
    if "${" in s:
        tail = s.split("}", 1)[1].lstrip("/")
        cands = [os.path.join(d, tail) for d in stock]
    for c in cands:
        for ext in ("", ".step", ".stp", ".wrl"):
            base = os.path.splitext(c)[0] if ext else c
            if os.path.isfile(base + ext if ext else c):
                return base + ext if ext else c
    return None


seen = {}
for fp in b.GetFootprints():
    key = str(fp.GetFPID().GetUniStringLibItemName())
    models = [m.m_Filename for m in fp.Models()]
    ok = [bool(resolve(m)) for m in models]
    seen.setdefault((key, tuple(models), tuple(ok)), []).append(fp.GetReference())
for (key, models, ok), refs in sorted(seen.items()):
    state = "OK " if models and all(ok) else "MISSING"
    print(f"{state} {key:45s} {','.join(refs)[:40]:40s} {models}")
print("stock dirs:", stock)
