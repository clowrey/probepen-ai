"""Write hw/.render.kicad_pcb: the board with every DNP flag cleared, so 3D renders and exports show the
hand-fitted parts too (AD9288, LCD, work-light LEDs, trimmers). It sits next to the real board so
${KIPRJMOD} still finds hw/lib/ppa.3dshapes.

    kicad-python docs/render_board.py
"""
from pathlib import Path

import pcbnew

HW = Path(__file__).resolve().parents[1] / "hw"
SRC = HW / "probepen_ai.kicad_pcb"
DST = HW / ".render.kicad_pcb"


def main() -> None:
    b = pcbnew.LoadBoard(str(SRC))
    n = 0
    for fp in b.GetFootprints():
        a = fp.GetAttributes()
        if a & pcbnew.FP_DNP:
            fp.SetAttributes(a & ~pcbnew.FP_DNP)
            n += 1
    pcbnew.SaveBoard(str(DST), b)
    print("wrote", DST, "with", n, "DNP parts shown")


if __name__ == "__main__":
    main()
