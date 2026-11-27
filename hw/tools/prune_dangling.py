"""Remove unused signal copper: vias and track ends that KiCad's DRC reports as dangling.

Fan-out and escape vias are laid before the router runs; when the router reaches a pad another way,
the via and its stub are left as an antenna. This removes every via DRC reports as dangling and every
track whose end DRC reports as dangling, except the inner-layer supply rails, which end in the open by
design. Run it a few times: removing a via can leave its stub dangling.

    kicad-python hw/tools/prune_dangling.py BOARD.kicad_pcb DRC.json
"""
import json
import sys

import pcbnew

KEEP = {"/GND", "/AGND", "/+3V3_DIG", "/+3V3_ANA", "/+1V1_CORE", "/+3V0_REF", "/+5V_VBUS", "/VBUS_RAW",
        "/+5V_ANA_IN", "/VREG_LX", "/VREG_AVDD", "/ADC_AVDD"}


def main(board_path, drc_path):
    b = pcbnew.LoadBoard(board_path)
    d = json.load(open(drc_path))
    want = {i["uuid"] for v in d["violations"] if v["type"] in ("via_dangling", "track_dangling") for i in v["items"]}
    gone = 0
    for t in list(b.GetTracks()):
        rail = t.GetNetname() in KEEP and t.GetClass() != "PCB_VIA" and t.GetLayer() in (pcbnew.In1_Cu, pcbnew.In2_Cu)
        if t.m_Uuid.AsString() in want and not rail:
            b.Remove(t)
            gone += 1
    pcbnew.SaveBoard(board_path, b)
    print("removed", gone, "dangling items")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
