"""Write hw/probepen_ai.kicad_pcb (138 x 17.6 mm S-curve tapered outline, 4-layer stackup,
placement, rules, zones) and hw/out/board_features.json from hw/design.py and hardware/pcb_datum.py.

Routing & placement rule: before routing traces, always check and optimize component rotations and
non-fixed support placement for direct signal flow (only nose J5/LEDs and tail USB-C J1 stay fixed).

Run with KiCad's Python after hw/gen_sch.py:
    kicad-python hw/gen_pcb.py

Coordinates in PLACE: mm relative to board datum (origin nose-tip center, +X toward USB-C tail,
Y centered -7.0..+7.0 nose tapering via smooth C1 S-curve x=18..28 to -8.80..+8.80 body, Y-down in KiCad).
On the KiCad page the board sits shifted by (OX, OY).
hw/out/board_features.json converts to right-handed CAD coordinates (origin nose-tip center, Y-up).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from hardware.pcb_datum import PCB as DATUM, half_width_at, point_in_pcb, scurve_params  # noqa: E402
from hw import design  # noqa: E402
from hw import regulator_ck  # noqa: E402
from hw.parts import PARTS  # noqa: E402

PCB_FILE = HERE / "probepen_ai.kicad_pcb"
DRU_FILE = HERE / "probepen_ai.kicad_dru"
FEATURES_FILE = HERE / "out" / "board_features.json"

_DEFAULT_FP_DIRS = (
    Path("/usr/share/kicad/footprints"),
    Path.home() / ".local" / "share" / "kicad" / "10.0" / "footprints",
)


def _find_kicad_fp() -> Path:
    env = os.environ.get("KICAD_FOOTPRINT_DIR")
    if env:
        return Path(env)
    for d in _DEFAULT_FP_DIRS:
        if d.is_dir():
            return d
    return _DEFAULT_FP_DIRS[-1]


KICAD_FP = _find_kicad_fp()
LOCAL_FP = HERE / "lib" / "ppa.pretty"

# Board mechanical envelope (from hardware/pcb_datum.py)
L = float(DATUM["length"])            # 138.0 mm (X: 0 .. 138)
W_NOSE = float(DATUM["nose_width"])   # 14.0 mm  (X: 0 .. 18)
HN_Y = W_NOSE / 2.0                   # 7.0 mm
TX0, TX1 = (float(v) for v in DATUM["taper_x"])  # 18.0 .. 28.0
W = float(DATUM["width"])             # 17.60 mm (X: 28 .. 138, exact 1.14" LCD glass width)
HW_Y = W / 2.0                        # 8.80 mm
THK = float(DATUM["thickness"])       # 1.6 mm
R_NOSE = float(DATUM["nose_radius"])  # 2.0 mm
R_TAIL = float(DATUM["tail_radius"])  # 2.0 mm

# KiCad drawing-sheet origin for datum (0, 0)
OX, OY = 20.0, 50.0

AFE_X0, AFE_X1 = DATUM["afe_cutout_x"]
AFE_Y0, AFE_Y1 = DATUM["afe_cutout_y"]
LCD_X0, LCD_X1 = DATUM["lcd_glass_x"]

MM = pcbnew.FromMM


def P(x: float, y: float) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I(MM(OX + x), MM(OY + y))


def to_features(x: float, y: float) -> tuple[float, float]:
    """KiCad datum (x right, y down) -> right-handed CAD datum (x right, y up)."""
    return round(x, 3), round(-y, 3)


def NET(name: str) -> str:
    return "/" + name


# ----------------------------------------------------------------------------------------------
# Component placement: ref -> (x, y, rot_deg) on F.Cu, or (x, y, rot_deg, "B") for B.Cu
# All populated SMT parts stay on F.Cu (single-sided JLCPCB assembly); no PCB mounting holes.
# J4 (13P solder-on LCD FPC landing), CT1 (optional DNP compensation trimmer), and digital TP_*
# sit on B.Cu cleanly separated from the LCD glass/FPC, while analog TP_* sit on F.Cu.
# ----------------------------------------------------------------------------------------------
def _jst_kicad_xy() -> tuple[float, float]:
    sx, sy_cad = DATUM["side_jst_xy"]
    return sx, -sy_cad  # CAD Y-up (+5.4) -> KiCad Y-down (-5.4)


PLACE: dict[str, tuple] = {
    # ---- Nose (x = 0..31): 3-in-1 terminal, worklight LEDs, HV dividers and their shunt parts
    "J5":   (*DATUM["nose_term_xy"], 0),
    "LED1": (*DATUM["led_top_xy"], 270, "B"),
    "LED2": (*DATUM["led_bot_xy"], 270, "B"),
    "R1":   (19.3, -2.4, 0),
    "R2":   (23.0, -2.4, 0),
    "C32":  (22.4, -4.1, 0),
    "C33":  (26.2, -2.7, 0),
    "R3U":  (26.2, -4.0, 0),
    "R3":   (26.2, -5.3, 0),
    "D4":   (29.3, -4.4, 0),
    "R1B":  (19.3, 2.4, 0),
    "R2B":  (23.0, 2.4, 0),
    "C32B": (22.4, 4.1, 0),
    "C33B": (26.2, 2.7, 0),
    "R3UB": (26.2, 4.0, 0),
    "R3B":  (26.2, 5.3, 0),
    "D4B":  (29.3, 4.4, 0),
    "CT1":  (21.2, -3.5, 0, "B"),
    "CT2":  (21.2, 3.5, 0, "B"),
    "H1":   (*DATUM["mount_hole_front_xy"], 0),

    # ---- Input buffer (x = 33): U3 turned so both inputs face the nose and both outputs the tail
    "U3":   (33.6, 0.0, 270),
    "C34":  (36.0, 1.3, 90),

    # ---- Analog supply and reference (x = 36..60, north strip; buttons own the south strip) ----
    "R51A": (36.6, -7.6, 0),
    "R51B": (36.6, -6.4, 0),
    "FB1":  (39.8, -7.3, 0),
    "C4":   (43.2, -7.3, 0),
    "U10":  (46.9, -6.3, 0),
    "C5":   (50.6, -7.3, 0),
    "U5": (46.5, 0.6, 0),
    "C7": (50.0, 1.4, 0),
    "R7": (50.0, 0.2, 0),
    "R7B": (50.0, -1.0, 0),
    "R7C": (50.0, -2.2, 0),
    "R8": (51.7, -3.4, 90),
    "C36": (43.0, -0.6, 90),

    "SW1":  (*DATUM["btn_ask_ai_xy"], 180),
    "SW2":  (*DATUM["btn_mode_xy"], 180),
    "SW3":  (*DATUM["btn_up_xy"], 180),
    "SW4":  (*DATUM["btn_down_xy"], 180),

    "TP_3V3A":  (40.0, -3.6, 0),
    "TP_AGND":  (43.4, -3.6, 0),
    "TP_3V0R": (44.5, 3.5, 0),
    "TP_REFM": (47.5, -3.3, 0),
    "TP_CH1": (62.30, -6.9, 0),
    "TP_CH2": (63.7, 5.65, 0),

    # ---- Gain stage (x = 63..72): U4 + U6 turned like U3, feedback parts stacked on the pins ----
    "U4": (55.25, 0.0, 270),
    "C39": (57.6, 3.9, 0),
    "R4A": (53.75, -3.9, 0),
    "R5A": (55.90, -3.9, 0),
    "C40A": (55.90, -5.0, 0),
    "R6A": (55.90, -6.1, 0),
    "R4B": (53.10, 3.9, 0),
    "R5B": (55.25, 3.9, 0),
    "C40B": (55.25, 5.0, 0),
    "R6B": (55.25, 6.1, 0),
    "U6": (59.80, 0.0, 270),
    "R24": (58.70, -6.9, 0),
    "R24B": (63.7, 7.5, 0),

    # ---- ADC (x = 73..90): input filters and reference caps at its pins, star ground beside it --
    "R25A": (63.70, -2.9, 0),
    "C42": (63.70, -4.1, 180),
    "C37A": (63.70, -1.6, 180),
    "C44": (63.70, -0.3, 180),
    "C37B": (63.70, 1.6, 180),
    "R25B": (63.70, 2.9, 0),
    "C42B": (63.70, 4.1, 180),
    "U2":   (*DATUM["ad9288_xy"], 0),
    "C46": (66.90, -5.6, 0),
    "R36A": (66.90, -6.8, 0),
    "C47": (66.90, 5.6, 0),
    "R36B": (66.90, 6.8, 0),
    "C52A": (69.10, -6.2, 0),
    "C52B": (69.10, 6.2, 0),
    "C49": (77.50, -0.5, 90),
    "C52C": (77.50, 2.0, 90),
    "R16": (77.90, -3.4, 90),
    "J4":   (*DATUM["lcd_fpc_xy"], 180, "B"),

    # LCD backlight switch and LCD supply cap, south-west of the ADC
    "R48": (116.5, 2.75, 0),
    "Q1": (115.2, 6.4, 0),
    "R47": (107.5, -7.3, 0),
    "C58": (69.10, 7.9, 0),

    # ---- MCU (x = 93..113): crystal on the ADC side at pins 30/31, regulator on the tail side ---
    "Y1": (83.70, 0.0, 180),
    "C30": (84.00, -2.2, 0),
    "C31": (82.40, 2.3, 0),
    "R20": (84.50, 2.3, 180),
    "R22": (105.0, -7.3, 0),
    "U1":   (*DATUM["rp2354b_xy"], 270),
    "C14": (96.90, -7.9, 0),
    "C15": (92.90, -7.9, 0),
    "C16": (94.5, 8.0, 0),
    "C17": (101.20, -3.4, 0),
    "C26": (94.90, -7.9, 0),
    "C28": (94.5, 7.0, 180),
    "C20": (98.4, 8.0, 0),
    "C29": (98.4, 7.0, 0),
    "R23": (100.4, 8.0, 0),
    "C41": (96.5, 7.0, 0),
    "C41B": (96.5, 8.0, 0),
    # Core regulator, copied from the owner's proven RP2354B layout (offsets from U1's centre)
    "R21": (101.20, -1.8, 0),
    "D6": (103.10, -3.95, 90),
    "R13": (116.50, 1.1, 0),
    "R12": (116.50, -0.4, 0),

    # ---- Tail (x = 113..138): side header + protection, digital LDO, USB protection, USB-C -----
    "J3":   (*_jst_kicad_xy(), 180),
    "R46": (119.10, -0.8, 180),
    "R45": (119.10, 0.4, 180),
    "R44": (119.10, 1.6, 180),
    "R43": (119.10, 2.8, 180),
    "D2": (123.00, 0.4, 90),
    "F2": (118.9, 4.8, 0),
    "C56": (121.5, 3.4, 0),
    "C3":   (119.4, 7.0, 0),
    "U9":   (123.2, 5.7, 180),
    "C2":   (127.0, 7.0, 0),
    "H2":   (*DATUM["mount_hole_rear_xy"], 0),
    "D1":   (127.6, -6.9, 0),
    "F1":   (131.8, -6.9, 0),
    "C1":   (134.2, -6.9, 270),
    "R10":  (135.3, -6.9, 90),
    "R11":  (136.4, -6.8, 90),
    "J1":   (*DATUM["usbc_xy"], 90),
    "R17":  (131.6, 7.0, 0),
    "R18":  (134.0, 7.0, 0),
    "C12":  (129.9, 7.2, 270),

    # ---- Bottom-side DNP test pads (outside the LCD glass / FPC, x = 32..80) -------------------
    "TP_5V": (122.8, 6.5, 0, "B"),
    "TP_GND1": (84.0, 7.6, 0, "B"),
    "TP_3V3D": (115.0, 6.5, 0, "B"),
    "TP_BOOT": (120.2, 6.5, 0, "B"),
    "TP_RUN": (120.2, -6.5, 0, "B"),
    "TP_1V1": (117.6, 6.5, 0, "B"),
    "TP_GND2": (122.8, -6.5, 0, "B"),
    "TP_SWCLK": (115.0, -6.5, 0, "B"),
    "TP_SWDIO": (117.6, -6.5, 0, "B"),
}


# Core regulator parts: CellKeeper's offsets from the centre of U1 (hw/regulator_ck.py)
_U1X, _U1Y = DATUM["rp2354b_xy"]
PLACE.update({ref: (round(_U1X + dx, 4), round(_U1Y + dy, 4), rot) for ref, (dx, dy, rot) in regulator_ck.PARTS.items()})

# 3D models kept in hw/lib/ppa.3dshapes (the RP2354B, inductor and USB-C models come from the owner's
# CellKeeper board library)
LOCAL_MODELS = {
    "U1": "RP2354B_QFN80.step",
    "L1": "Inductor_2016_AOTA-B201610.step",
    "J1": "USB_C_Receptacle_HRO_TYPE-C-31-M-12.step",
}

# ----------------------------------------------------------------------------------------------
_io = pcbnew.PCB_IO_KICAD_SEXPR()


def load_fp(fpid: str) -> pcbnew.FOOTPRINT:
    lib, name = fpid.split(":")
    path = LOCAL_FP if lib == "ppa" else HERE / "lib" / "rpi.pretty" if lib == "rpi" else KICAD_FP / f"{lib}.pretty"
    fp = _io.FootprintLoad(str(path), name)
    if fp is None:
        raise FileNotFoundError(f"Footprint not found: {fpid} (searched {path})")
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def seg(board: pcbnew.BOARD, layer: int, a, b, w: float = 0.1):
    s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(layer)
    s.SetStart(P(*a))
    s.SetEnd(P(*b))
    s.SetWidth(MM(w))
    board.Add(s)
    return s


def arc_geom(board: pcbnew.BOARD, layer: int, start, mid, end, w: float = 0.1):
    s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
    s.SetLayer(layer)
    s.SetArcGeometry(P(*start), P(*mid), P(*end))
    s.SetWidth(MM(w))
    board.Add(s)
    return s


def outline(board: pcbnew.BOARD):
    """Closed 138 x 17.60 mm pen outline (untapered when W_NOSE == W, otherwise C1 S-curve taper)."""
    rn, rt, k = R_NOSE, R_TAIL, 1.0 - math.sqrt(0.5)
    e = pcbnew.Edge_Cuts

    if abs(W - W_NOSE) < 1e-6:
        hb = HW_Y
        seg(board, e, (rn, -hb), (L - rt, -hb))
        arc_geom(board, e, (L - rt, -hb), (L - rt * k, -hb + rt * k), (L, -hb + rt))
        seg(board, e, (L, -hb + rt), (L, hb - rt))
        arc_geom(board, e, (L, hb - rt), (L - rt * k, hb - rt * k), (L - rt, hb))
        seg(board, e, (L - rt, hb), (rn, hb))
        arc_geom(board, e, (rn, hb), (rn * k, hb - rn * k), (0.0, hb - rn))
        seg(board, e, (0.0, hb - rn), (0.0, -hb + rn))
        arc_geom(board, e, (0.0, -hb + rn), (rn * k, -hb + rn * k), (rn, -hb))
        return

    tx0, tx1, x_mid, y_mid, r_s, hb = scurve_params()
    theta = math.atan2(x_mid - tx0, r_s - (y_mid - HN_Y))
    ht = 0.5 * theta
    # Midpoints of Arc 1 (18..23) and Arc 2 (23..28) along their circles
    x_m1 = tx0 + r_s * math.sin(ht)
    y_m1 = HN_Y + r_s - r_s * math.cos(ht)
    x_m2 = tx1 - r_s * math.sin(ht)
    y_m2 = hb - r_s + r_s * math.cos(ht)

    # Top edge (KiCad -Y), left -> right
    seg(board, e, (rn, -HN_Y), (tx0, -HN_Y))
    arc_geom(board, e, (tx0, -HN_Y), (x_m1, -y_m1), (x_mid, -y_mid))
    arc_geom(board, e, (x_mid, -y_mid), (x_m2, -y_m2), (tx1, -hb))
    seg(board, e, (tx1, -hb), (L - rt, -hb))
    # Tail end
    arc_geom(board, e, (L - rt, -hb), (L - rt * k, -hb + rt * k), (L, -hb + rt))
    seg(board, e, (L, -hb + rt), (L, hb - rt))
    arc_geom(board, e, (L, hb - rt), (L - rt * k, hb - rt * k), (L - rt, hb))
    # Bottom edge (KiCad +Y), right -> left
    seg(board, e, (L - rt, hb), (tx1, hb))
    arc_geom(board, e, (tx1, hb), (x_m2, y_m2), (x_mid, y_mid))
    arc_geom(board, e, (x_mid, y_mid), (x_m1, y_m1), (tx0, HN_Y))
    seg(board, e, (tx0, HN_Y), (rn, HN_Y))
    # Nose end
    arc_geom(board, e, (rn, HN_Y), (rn * k, HN_Y - rn * k), (0.0, HN_Y - rn))
    seg(board, e, (0.0, HN_Y - rn), (0.0, -HN_Y + rn))
    arc_geom(board, e, (0.0, -HN_Y + rn), (rn * k, -HN_Y + rn * k), (rn, -HN_Y))


def text(board: pcbnew.BOARD, s: str, x: float, y: float, size: float = 0.9,
         layer: int = pcbnew.F_SilkS, rot: float = 0, bold: bool = False):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(P(x, y))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
    t.SetTextThickness(MM(size * (0.2 if bold else 0.15)))
    t.SetTextAngleDegrees(rot)
    if layer in (pcbnew.B_SilkS, pcbnew.B_Fab):
        t.SetMirrored(True)
    board.Add(t)
    return t


def zone(board: pcbnew.BOARD, net, layers: list[int], pts: list[tuple[float, float]],
         priority: int = 0, rule_area: bool = False, full: bool = False,
         clearance: float = 0.2, name: str = "", min_thickness: float = 0.2):
    z = pcbnew.ZONE(board)
    ls = pcbnew.LSET()
    for layer in layers:
        ls.AddLayer(layer)
    z.SetLayerSet(ls)
    poly = z.Outline()
    poly.NewOutline()
    for x, y in pts:
        poly.Append(MM(OX + x), MM(OY + y))
    if rule_area:
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(False)
        z.SetDoNotAllowVias(False)
        z.SetDoNotAllowZoneFills(True)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
    else:
        z.SetNet(net)
        z.SetAssignedPriority(priority)
        z.SetMinThickness(MM(min_thickness))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL if full else pcbnew.ZONE_CONNECTION_THT_THERMAL)
        z.SetThermalReliefGap(MM(0.25))
        z.SetThermalReliefSpokeWidth(MM(0.35))
        z.SetLocalClearance(MM(clearance))
    if name:
        z.SetZoneName(name)
    board.Add(z)
    return z


def write_dru():
    """JLCPCB 4-layer design rules plus USB / high-speed / AFE clearances."""
    dru = """(version 1)
# Generated by hw/gen_pcb.py. JLCPCB JLC04161H-7628 4-layer manufacturing rules.
(rule "JLC 4-layer baseline"
  # JLC 4-layer 3.5-mil (0.092mm; 5-mil 0.127mm nominal)
  (constraint clearance (min 0.092mm))
  (constraint track_width (min 0.092mm))
  (constraint via_diameter (min 0.40mm))
  (constraint via_drill (min 0.20mm))
  (constraint hole_clearance (min 0.25mm))
  (constraint edge_clearance (min 0.30mm))
  (constraint silk_clearance (min 0.15mm))
)
(rule "POWER netclass"
  (condition "A.NetClass == 'POWER'")
  (constraint track_width (min 0.12mm))
)
(rule "USB diff pair"
  (condition "A.NetClass == 'USB'")
  (constraint track_width (min 0.12mm))
)
(rule "HV divider to ground and LV"
  (condition "(A.NetName == '/CH1_TIP' || A.NetName == '/CH1_HV_MID' || A.NetName == '/CH2_TIP' || A.NetName == '/CH2_HV_MID') && (B.NetName != A.NetName)")
  (constraint clearance (min 0.40mm))
)
"""
    DRU_FILE.write_text(dru, encoding="utf8", newline="\n")


def _find_kicad_cli() -> str:
    return (
        os.environ.get("KICAD_CLI")
        or shutil.which("kicad-cli")
        or str(Path.home() / ".local" / "bin" / "kicad-cli")
    )


def schematic_nets() -> dict[tuple[str, str], str]:
    """(ref, pin) -> net name exported from hw/probepen_ai.kicad_sch."""
    out = Path(tempfile.gettempdir()) / "probepen_ai_netlist.net"
    subprocess.run(
        [_find_kicad_cli(), "sch", "export", "netlist", "--format", "kicadsexpr",
         "-o", str(out), str(HERE / "probepen_ai.kicad_sch")],
        check=True, capture_output=True, text=True,
    )
    txt = out.read_text(encoding="utf8")
    pins: dict[tuple[str, str], str] = {}
    for m in re.finditer(r'\(net\s+\(code "?\d+"?\)\s+\(name "([^"]*)"\)(.*?)(?=\n\s*\(net\s|\Z)', txt, re.S):
        for ref, pin in re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', m.group(2)):
            pins[(ref, pin)] = m.group(1)
    return pins


# ----------------------------------------------------------------------------------------------
# Silkscreen collision avoidance
# ----------------------------------------------------------------------------------------------
SILK_GAP = 0.2


def _silk_obstacles(board: pcbnew.BOARD, layer: int, skip=None):
    edge_clearance = 0.4
    shapes = []
    for d in board.Drawings():
        if d.GetLayer() in (layer, pcbnew.Edge_Cuts) and (skip is None or d.m_Uuid != skip.m_Uuid):
            shapes.append(d.GetEffectiveShape())
    for fp in board.GetFootprints():
        for it in fp.GraphicalItems():
            if it.GetLayer() in (layer, pcbnew.Edge_Cuts):
                shapes.append(it.GetEffectiveShape())
        for pad in fp.Pads():
            cu_layer = pcbnew.B_Cu if layer == pcbnew.B_SilkS else pcbnew.F_Cu
            if pad.IsOnLayer(cu_layer):
                shapes.append(pad.GetEffectiveShape(cu_layer))
            if pad.GetDrillSizeX():
                shapes.append(pad.GetEffectiveHoleShape())
    return shapes, edge_clearance


def _inside_board(bb, margin: float = 0.4) -> bool:
    x0 = bb.GetLeft() / 1e6 - OX
    x1 = bb.GetRight() / 1e6 - OX
    y0 = bb.GetTop() / 1e6 - OY
    y1 = bb.GetBottom() / 1e6 - OY
    for cx in (x0, x1):
        for cy in (y0, y1):
            if not point_in_pcb(cx, cy, margin=margin):
                return False
    for sx in range(int(math.ceil(max(x0, TX0))), int(math.floor(min(x1, TX1))) + 1):
        for cy in (y0, y1):
            if not point_in_pcb(float(sx), cy, margin=margin):
                return False
    return True


def _clear(item, obstacles, gap: float = SILK_GAP, margin: float = 0.4) -> bool:
    if not _inside_board(item.GetBoundingBox(), margin=margin):
        return False
    me = item.GetEffectiveShape()
    return not any(me.Collide(o, MM(gap)) for o in obstacles)


def place_references(board: pcbnew.BOARD):
    """Place visible reference designators cleanly clear of pads/silk/edge; hide crowded 0402 refs."""
    for silk_layer in (pcbnew.F_SilkS, pcbnew.B_SilkS):
        fps = sorted(
            (
                fp for fp in board.GetFootprints()
                if fp.Reference().IsVisible()
                and ((fp.GetLayer() == pcbnew.B_Cu) == (silk_layer == pcbnew.B_SilkS))
            ),
            key=lambda f: f.GetBoundingBox(False).GetArea(),
        )
        obstacles, margin = _silk_obstacles(board, silk_layer)
        placed = []
        hidden = []
        for fp in fps:
            ref = fp.Reference()
            if _clear(ref, obstacles + placed, margin=margin):
                placed.append(ref.GetEffectiveShape())
                continue
            bb = fp.GetBoundingBox(False)
            cx, cy = bb.GetCenter().x, bb.GetCenter().y
            hw, hh = bb.GetWidth() / 2, bb.GetHeight() / 2
            ok = False
            for d in (0.15, 0.4, 0.8, 1.2):
                gap = MM(d + 0.5)
                spots = [
                    (cx, cy - hh - gap, 0), (cx, cy + hh + gap, 0),
                    (cx - hw - gap, cy, 90), (cx + hw + gap, cy, 90),
                    (cx - hw - gap - MM(0.7), cy, 0), (cx + hw + gap + MM(0.7), cy, 0),
                ]
                for x, y, rot in spots:
                    ref.SetTextAngleDegrees(rot)
                    ref.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                    if _clear(ref, obstacles + placed, margin=margin):
                        ok = True
                        break
                if ok:
                    break
            if ok:
                placed.append(ref.GetEffectiveShape())
            else:
                ref.SetVisible(False)
                hidden.append(fp.GetReference())
        if hidden:
            print(f"{board.GetLayerName(silk_layer)}: hid {len(hidden)} crowded refs")


def fit_text(board: pcbnew.BOARD, t: pcbnew.PCB_TEXT, spots, what: str) -> bool:
    layer = t.GetLayer()
    obstacles, margin = _silk_obstacles(board, layer, skip=t)
    for x, y, rot, size in spots:
        t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
        t.SetTextThickness(MM(size * (0.2 if t.IsBold() else 0.15)))
        t.SetTextAngleDegrees(rot)
        t.SetPosition(P(x, y))
        if _clear(t, obstacles, margin=margin):
            return True
    board.Remove(t)
    print(f"skipped silk label {what!r} (no clear spot)")
    return False


def silkscreen(board: pcbnew.BOARD):
    """Top and bottom functional labels fitted away from pads and edges."""
    F, B = pcbnew.F_SilkS, pcbnew.B_SilkS
    for lbl, ny in (("CH1", -4.0), ("GND", 0.0), ("CH2", 4.0)):
        tf = text(board, lbl, 16.8, ny, 0.8, layer=F, bold=True)
        fit_text(board, tf, [(x, ny + dy, 0, 0.8) for x in (17.2, 16.4, 15.8) for dy in (0, -1.8, 1.8)], f"nose_f_{lbl}")
        tb = text(board, lbl, 17.5, ny, 0.8, layer=B, bold=True)
        fit_text(board, tb, [(x, ny + dy, 0, 0.8) for x in (17.6, 16.8, 2.2) for dy in (0, -1.8, 1.8)], f"nose_b_{lbl}")

    btn_labels = [
        ("AI", DATUM["btn_ask_ai_xy"][0]),
        ("MODE", DATUM["btn_mode_xy"][0]),
        ("UP", DATUM["btn_up_xy"][0]),
        ("DN", DATUM["btn_down_xy"][0]),
    ]
    for lbl, sx in btn_labels:
        t = text(board, lbl, sx, 4.8, 0.8, layer=F, bold=True)
        fit_text(board, t, [(sx + dx, y, 0, 0.8) for y in (4.8, 4.3, 3.6, 5.2, 5.6, 7.6) for dx in (0, -2.5, 2.5, 4.0, -4.0)], lbl)

    # Back side labels strictly outside LCD glass (x=32..63) & FPC (63..78.85)
    title = text(board, "ProbePen-AI v1.0", 90.0, -0.5, 1.05, layer=B, bold=True)
    fit_text(board, title, [(x, y, 0, 1.05) for y in (-0.5, 0.5, -1.6) for x in (90.0, 92.5, 25.0)], "title")
    pod = text(board, "IO0-3 3V3 GND", 119.5, -2.0, 0.8, layer=B, bold=True)
    fit_text(board, pod, [(119.5 + dx, y, 0, 0.8) for y in (-2.0, -1.0, -6.8) for dx in (0, -2.0, 2.0)], "POD")
    lcd_hint = text(board, "1.14/0.96 13P LCD", 89.5, 2.2, 0.8, layer=B, bold=True)
    fit_text(board, lcd_hint, [(x, y, 0, 0.8) for y in (2.2, 3.1, 1.3) for x in (89.5, 92.5)], "LCD")

    # Short names next to each test pad on its own layer (analog on F, digital on B)
    tp_labels = {
        "TP_AGND": "AGND", "TP_3V3A": "3V3A", "TP_3V0R": "3V0R",
        "TP_CH1": "CH1", "TP_CH2": "CH2", "TP_REFM": "REF",
        "TP_3V3D": "3V3D", "TP_5V": "5V", "TP_1V1": "1V1",
        "TP_GND2": "GND", "TP_SWCLK": "SWCLK", "TP_SWDIO": "SWDIO", "TP_RUN": "RUN",
        "TP_BOOT": "BOOT", "TP_GND1": "GND",
    }
    for ref, lbl in tp_labels.items():
        loc = PLACE[ref]
        tx, ty = loc[0], loc[1]
        lay = B if (len(loc) > 3 and loc[3] == "B") else F
        t = text(board, lbl, tx, ty + 1.3, 0.8, layer=lay)
        spots = [(tx, ty + dy, 0, 0.8) for dy in (1.3, -1.3, 1.7, -1.7)] + [
            (tx + dx, ty, 0, 0.8) for dx in (2.1, -2.1, 2.7, -2.7)
        ] + [(tx + dx, ty + dy, 0, 0.8) for dx in (-1.9, 1.9) for dy in (-1.3, 1.3)] + [
            (tx + dx, ty + dy, 90, 0.8) for dx in (-1.7, 0.0, 1.7) for dy in (-1.7, 0.0, 1.7)
        ]
        fit_text(board, t, spots, ref)


# ----------------------------------------------------------------------------------------------
def build() -> pcbnew.BOARD:
    board = pcbnew.NewBoard(str(PCB_FILE))
    board.SetCopperLayerCount(4)
    ds = board.GetDesignSettings()
    ds.SetBoardThickness(MM(THK))
    ds.SetAuxOrigin(P(0.0, HW_Y))
    ds.m_TrackMinWidth = MM(0.092)
    ds.m_MinClearance = MM(0.092)
    ds.m_ViasMinSize = MM(0.40)
    ds.m_ViasMinDrill = MM(0.20)
    ds.m_CopperEdgeClearance = MM(0.30)
    ds.m_HoleClearance = MM(0.25)
    ds.m_HoleToHoleMin = MM(0.25)

    pin_nets = schematic_nets()
    nets: dict[str, pcbnew.NETINFO_ITEM] = {}
    for name in sorted(set(pin_nets.values())):
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
        nets[name.lstrip("/")] = n

    outline(board)

    seen_refs = set()
    for c in design.COMPONENTS:
        assert c.ref in PLACE, f"Missing PLACE entry for {c.ref}"
        seen_refs.add(c.ref)
        loc = PLACE[c.ref]
        x, y, rot = loc[0], loc[1], loc[2]
        side = loc[3] if len(loc) > 3 else "F"

        fp = load_fp(c.footprint)
        fp.SetReference(c.ref)
        fp.SetValue(c.value)
        board.Add(fp)
        fp.SetPosition(P(x, y))
        fp.SetOrientationDegrees(rot)
        if side == "B":
            fp.Flip(P(x, y), False)
        fp.SetPath(pcbnew.KIID_PATH(f"/{design.ROOT_UUID}/{c.uuid}"))

        if c.part:
            fp.SetField("LCSC", c.lcsc)
            fp.GetField("LCSC").SetVisible(False)
            fp.SetField("MPN", PARTS[c.part].mpn)
            fp.GetField("MPN").SetVisible(False)

        if c.ref in ("J1", "J3", "J4", "J5", "U1"):
            fp.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        if c.ref in ("LED1", "LED2"):                 # hand-soldered: thermal relief to any pour
            fp.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        if c.ref in LOCAL_MODELS:                     # parts KiCad's library has no 3D model for
            fp.Models().clear()
            m3d = pcbnew.FP_3DMODEL()
            m3d.m_Filename = "${KIPRJMOD}/lib/ppa.3dshapes/" + LOCAL_MODELS[c.ref]
            fp.Models().push_back(m3d)
        if c.ref == "J1":
            for it in list(fp.GraphicalItems()):
                if it.GetLayer() == pcbnew.F_SilkS and it.GetBoundingBox().GetRight() / 1e6 - OX > L - 0.15:
                    fp.Remove(it)

        attrs = fp.GetAttributes()
        if c.dnp:
            attrs |= pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES | pcbnew.FP_DNP
        fp.SetAttributes(attrs)
        if c.dnp:
            fp.SetDNP(True)

        for pad in fp.Pads():
            net = pin_nets.get((c.ref, pad.GetNumber()))
            if net:
                if not net.startswith("unconnected-"):
                    assert net.lstrip("/") == c.pins.get(pad.GetNumber(), net.lstrip("/")), (c.ref, pad.GetNumber(), net)
                pad.SetNet(nets[net.lstrip("/")])

        fp.Reference().SetTextSize(pcbnew.VECTOR2I(MM(0.8), MM(0.8)))
        fp.Reference().SetTextThickness(MM(0.12))
        if c.ref in ("J1", "J3", "J4", "J5", "SW1", "SW2", "SW3", "SW4", "H1", "H2") or c.ref.startswith("TP_"):
            fp.Reference().SetVisible(False)

    extra_place = set(PLACE) - seen_refs
    assert not extra_place, f"Unused PLACE entries: {extra_place}"

    # ---- Keepouts & 4-layer copper planes (In1=GND/AGND, In2=+3V3_DIG/+3V3_ANA) ----------
    def _circle_pts(cx: float, cy: float, r: float, steps: int = 24) -> list[tuple[float, float]]:
        return [
            (round(cx + r * math.cos(2.0 * math.pi * k / steps), 4),
             round(cy + r * math.sin(2.0 * math.pi * k / steps), 4))
            for k in range(steps)
        ]

    inner_layers = [pcbnew.In1_Cu, pcbnew.In2_Cu]
    zone(
        board, None, inner_layers,
        [(AFE_X0, AFE_Y0), (AFE_X1, AFE_Y0), (AFE_X1, AFE_Y1), (AFE_X0, AFE_Y1)],
        rule_area=True, name="AFE high-Z keepout",
    )
    for href in ("H1", "H2"):
        hx, hy = PLACE[href][:2]
        r_npth_m2 = 1.45  # 1.10 mm radius + 0.35 mm circular inner-plane clearance around unplated M2 hole
        zone(
            board, None, inner_layers,
            _circle_pts(hx, hy, r_npth_m2),
            rule_area=True, name=f"{href} NPTH keepout",
        )
    jx = DATUM["usbc_xy"][0] - 2.6
    for sign, tag in ((-1, "top"), (1, "bot")):
        jy = sign * 2.89
        r_npth = 0.65
        zone(
            board, None, inner_layers,
            _circle_pts(jx, jy, r_npth),
            rule_area=True, name=f"J1 NPTH keepout {tag}",
        )

    inset = 0.45
    rn_i = max(0.2, R_NOSE - inset)
    rt_i = max(0.2, R_TAIL - inset)
    def _arc_pts(cx, cy, r, a0_deg, a1_deg, steps=6):
        return [
            (round(cx + r * math.cos(math.radians(a0_deg + (a1_deg - a0_deg) * k / steps)), 4),
             round(cy + r * math.sin(math.radians(a0_deg + (a1_deg - a0_deg) * k / steps)), 4))
            for k in range(steps + 1)
        ]

    # Sample smooth S-curve inset polygon points along x=18..28
    taper_xs = [TX0 + (TX1 - TX0) * k / 10.0 for k in range(11)]
    taper_top = [(round(x, 4), round(-half_width_at(x, margin=inset), 4)) for x in taper_xs]
    taper_bot = [(round(x, 4), round(half_width_at(x, margin=inset), 4)) for x in reversed(taper_xs)]

    # No copper is poured nose-side of the input dividers (x < AFE_X1) on any layer: the probe pads and the
    # 1 Mohm nodes see no plane, and no ring of ground is left around the nose. AGND reaches the centre
    # terminal pad by one wide track down the middle.
    body_xs = [AFE_X1 + (TX1 - AFE_X1) * k / 4.0 for k in range(5)]
    body_top = [(round(x, 4), round(-half_width_at(x, margin=inset), 4)) for x in body_xs]
    body_bot = [(round(x, 4), round(half_width_at(x, margin=inset), 4)) for x in reversed(body_xs)]
    gnd_in1_poly = [
        *body_top,
        *_arc_pts(L - R_TAIL, -HW_Y + R_TAIL, rt_i, 270, 360),
        *_arc_pts(L - R_TAIL, HW_Y - R_TAIL, rt_i, 0, 90),
        *body_bot,
    ]
    zone(board, nets["GND"], [pcbnew.In1_Cu], gnd_in1_poly, priority=0, clearance=0.22, name="In1 GND", min_thickness=0.14)

    # Analog island: everything nose-side of the ADC's digital half, except the south strip that
    # carries the buttons. AGND on In1.Cu, +3V3_ANA on In2.Cu, and AGND again as a pour on F.Cu and
    # B.Cu. Digital GND / +3V3_DIG own the rest of the board, with GND pours on F.Cu and B.Cu.
    isl_x1, isl_y1 = float(DATUM["ad9288_xy"][0]) + 1.7, 4.45
    island = [
        *body_top,
        (isl_x1, round(-HW_Y + inset, 4)), (isl_x1, isl_y1), (31.0, isl_y1), (31.0, round(HW_Y - inset, 4)),
        *body_bot,
    ]
    zone(board, nets["AGND"], [pcbnew.In1_Cu], island, priority=1, clearance=0.40, name="In1 AGND", min_thickness=0.14)
    zone(board, nets["+3V3_DIG"], [pcbnew.In2_Cu], gnd_in1_poly, priority=0, clearance=0.22, name="In2 +3V3_DIG", min_thickness=0.14)
    # the ADC's digital supply pins (15, 46) sit just tail-side of its analog pins: +3V3_ANA stops short of them
    # +3V3_ANA reaches the ADC's centre (its analog supply pins 30/31 tap there) but stops short of the
    # ADC's digital supply pins 15 / 46, which sit at the top and bottom just tail-side of the analog pins
    ana_x1, ana_y = isl_x1 - 4.4, 3.2
    ana_3v3 = [(AFE_X1, round(-HW_Y + inset, 4)), (ana_x1, round(-HW_Y + inset, 4)), (ana_x1, -ana_y),
               (isl_x1, -ana_y), (isl_x1, ana_y), (ana_x1, ana_y), (ana_x1, isl_y1),
               (31.0, isl_y1), (31.0, round(HW_Y - inset, 4)), (AFE_X1, round(HW_Y - inset, 4))]
    zone(board, nets["+3V3_ANA"], [pcbnew.In2_Cu], ana_3v3, priority=1, clearance=0.40, name="In2 +3V3_ANA", min_thickness=0.14)
    for layer, tag in ((pcbnew.F_Cu, "F"), (pcbnew.B_Cu, "B")):
        zone(board, nets["GND"], [layer], gnd_in1_poly, priority=0, clearance=0.20, name=f"{tag} GND pour", min_thickness=0.15)
        zone(board, nets["AGND"], [layer], island, priority=1, clearance=0.25, name=f"{tag} AGND pour", min_thickness=0.15)

    # ---- Core regulator copper copied from CellKeeper: local pours, +1V1 inside the pad ring, cut-outs ----
    def rel(pts):
        return [(round(_U1X + x, 4), round(_U1Y + y, 4)) for x, y in pts]

    for net_name, prio, pts in regulator_ck.ZONES_F:
        zone(board, nets[net_name], [pcbnew.F_Cu], rel(pts), priority=prio, full=True, clearance=0.15,
             name=f"regulator {net_name}", min_thickness=0.1)
    zone(board, None, [pcbnew.F_Cu], rel(regulator_ck.NO_POUR_F), rule_area=True, name="regulator inductor no-pour")
    ko = zone(board, None, inner_layers, rel(regulator_ck.NO_INNER), rule_area=True, name="regulator switch-node cut-out")
    ko.SetDoNotAllowTracks(True)
    ko.SetDoNotAllowVias(True)

    silkscreen(board)
    place_references(board)
    return board


def features(board: pcbnew.BOARD) -> dict:
    """Export mechanical anchors in right-handed CAD coordinates (origin nose-tip center, Y-up)."""
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}

    def _cad_box_from_kicad(bx0, by0, bx1, by1):
        return [round(bx0, 3), round(-by1, 3), round(bx1, 3), round(-by0, 3)]

    x1, y1 = PLACE["J1"][:2]
    x3, y3 = PLACE["J3"][:2]
    x4, y4 = PLACE["J4"][:2]
    x5, y5 = PLACE["J5"][:2]
    cons = [
        {
            "ref": "J1", "kind": "usbc", "edge": "tail",
            "x": to_features(x1, y1)[0], "y": to_features(x1, y1)[1],
            "w": 9.0, "h": 7.35, "z_max": 3.25,
            "plug_envelope": [round(L, 3), -5.0, -0.5, round(L + 16.0, 3), 5.0, 5.0],
        },
        {
            "ref": "J5", "kind": "wago_2060_3p_combo", "edge": "nose",
            "x": to_features(x5, y5)[0], "y": to_features(x5, y5)[1],
            "w": 13.1, "h": 11.9, "z_max": 4.5,
            "plug_envelope": [-15.0, -6.2, -2.5, 16.0, 6.2, 5.0],
        },
        {
            "ref": "J3", "kind": "jst_sh6", "edge": "+y",
            "x": to_features(x3, y3)[0], "y": to_features(x3, y3)[1],
            "w": 8.0, "h": 4.25, "z_max": 2.9,
            "plug_envelope": [round(x3 - 5.0, 3), round(HW_Y - 1.5, 3), 1.4, round(x3 + 5.0, 3), round(HW_Y + 12.0, 3), 5.2],
        },
        {
            "ref": "J4", "kind": "lcd_fpc_13p", "edge": "internal", "side": "B",
            "x": to_features(x4, y4)[0], "y": to_features(x4, y4)[1],
            "w": 3.0, "h": 8.8, "z_max": 0.2,
            "plug_envelope": [round(LCD_X0, 3), round(-float(DATUM["lcd_glass_width"]) / 2.0, 3),
                              -float(DATUM["lcd_glass_thickness"]) - 0.2,
                              round(LCD_X1 + 15.85, 3), round(float(DATUM["lcd_glass_width"]) / 2.0, 3), 0.1],
        },
    ]

    centres = {}
    for ref, fp in fps.items():
        pads = list(fp.Pads())
        if not pads:
            continue
        xs = [b for p in pads for b in (p.GetBoundingBox().GetLeft(), p.GetBoundingBox().GetRight())]
        ys = [b for p in pads for b in (p.GetBoundingBox().GetTop(), p.GetBoundingBox().GetBottom())]
        centres[ref] = list(to_features((min(xs) + max(xs)) / 2e6 - OX, (min(ys) + max(ys)) / 2e6 - OY))

    lx0, lx1 = DATUM["lcd_active_x"]
    ahw = float(DATUM["lcd_active_width"]) / 2.0
    holes = []
    for href in ("H1", "H2"):
        hx, hy = to_features(*PLACE[href][:2])
        holes.append([hx, hy, 2.2])
    return {
        "outline_mm": [L, W],
        "nose_width": W_NOSE,
        "taper_x": [TX0, TX1],
        "thickness": THK,
        "nose_r": R_NOSE,
        "tail_r": R_TAIL,
        "holes": holes,
        "connectors": cons,
        "leds": [list(to_features(*PLACE[r][:2])) for r in ("LED1", "LED2")],
        "buttons": [list(to_features(*PLACE[r][:2])) for r in ("SW1", "SW2", "SW3", "SW4")],
        "lcd_side": "B",
        "lcd_glass": [LCD_X0, round(-float(DATUM["lcd_glass_width"]) / 2.0, 3),
                      LCD_X1, round(float(DATUM["lcd_glass_width"]) / 2.0, 3)],
        "lcd_window": [lx0, round(-ahw, 3), lx1, round(ahw, 3)],
        "afe_keepout": _cad_box_from_kicad(AFE_X0, AFE_Y0, AFE_X1, AFE_Y1),
        "part_centres": centres,
        "max_component_height": 3.3,
        "max_under_lcd_height": 0.0,
    }


def main():
    write_dru()
    board = build()
    board.BuildConnectivity()
    pcbnew.SaveBoard(str(PCB_FILE), board)
    FEATURES_FILE.parent.mkdir(exist_ok=True)
    FEATURES_FILE.write_text(json.dumps(features(board), indent=1), encoding="utf8")
    print("wrote", PCB_FILE, "and", FEATURES_FILE)


if __name__ == "__main__":
    main()
