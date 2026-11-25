import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HW = ROOT / "hw"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KICAD_DIR = Path(os.environ.get("KICAD_DIR", "/mnt/c/Program Files/KiCad/10.0"))
KICAD_BIN = KICAD_DIR / "bin"
KPY = KICAD_BIN / "python.exe"
KCLI = KICAD_BIN / "kicad-cli.exe"
KICAD_SHARE = KICAD_DIR / "share" / "kicad"
