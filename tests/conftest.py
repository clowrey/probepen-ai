import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HW = ROOT / "hw"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KCLI = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or str(Path.home() / ".local/bin/kicad-cli")
KPY = os.environ.get("KICAD_PYTHON") or shutil.which("kicad-python") or str(Path.home() / ".local/bin/kicad-python")
_DEFAULT_SHARE = Path.home() / ".local/share/kicad/10.0"
KICAD_SHARE = Path(os.environ.get("KICAD_SHARE", _DEFAULT_SHARE if _DEFAULT_SHARE.is_dir() else "/usr/share/kicad"))


def kpy(script, *args, check=True):
    """Run a script with KiCad's bundled Python (has pcbnew)."""
    cmd = [KPY, str(script), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if check and r.returncode:
        raise AssertionError(f"{script} failed:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def kcli(*args, check=True):
    """Run local KiCad 10 kicad-cli."""
    cmd = [KCLI, *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if check and r.returncode:
        raise AssertionError(f"kicad-cli {args} failed:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r
