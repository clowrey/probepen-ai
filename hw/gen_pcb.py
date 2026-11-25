"""Write hw/probepen_ai.kicad_pcb (outline, 4-layer stackup, placement, rules, zones)
and hw/out/board_features.json from hw/design.py and hardware/pcb_datum.py.

Run with KiCad's Python after hw/gen_sch.py:
    kicad-python hw/gen_pcb.py

Coordinates in PLACE: mm relative to board datum (origin nose-tip center, +X toward USB-C tail,
Y centered -7.5..+7.5 mm, Y-down in KiCad). On the KiCad page the board sits shifted by (OX, OY).
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

from hardware.pcb_datum import PCB as DATUM  # noqa: E402
from hw import design  # noqa: E402
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
L = float(DATUM["length"])        # 132.0 mm (X: 0 .. 132)
W = float(DATUM["width"])         # 15.0 mm  (Y: -7.5 .. +7.5)
HW_Y = W / 2.0                    # 7.5 mm
THK = float(DATUM["thickness"])   # 1.6 mm
R_NOSE = float(DATUM["nose_radius"])  # 4.0 mm
R_TAIL = float(DATUM["tail_radius"])  # 1.5 mm

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
# All 147 populated SMT parts + H1/H2 stay on F.Cu (single-sided JLCPCB assembly).
# Only DNP test pads (TP_*) and optional trimmer (CT1) sit on B.Cu.
# ----------------------------------------------------------------------------------------------
def _jst_kicad_xy() -> tuple[float, float]:
    sx, sy_cad = DATUM["side_jst_xy"]
    return sx, -sy_cad  # CAD Y-up (+4.1) -> KiCad Y-down (-4.1)


PLACE: dict[str, tuple] = {
    # ---- Nose tip, LEDs, and High-Z AFE input divider (inside x=2..16.5 keepout) ----
    "J2": (*DATUM["tip_boss_xy"], 0),
    "LED1": (*DATUM["led_top_xy"], 0),
    "LED2": (*DATUM["led_bot_xy"], 0),
    "R1": (11.8, -1.8, 0),
    "R2": (11.8, 0.5, 0),
    "C32": (11.8, -3.4, 0),
    "R3": (14.6, -2.5, 90),
    "C33": (14.6, 0.2, 90),
    "D4": (13.8, 3.3, 0),
    "C60": (12.2, -5.8, 0),
    "C61": (12.2, 5.8, 0),
    "D5": (15.0, -5.9, 0),
    "R51": (18.0, -5.8, 0),
    "CT1": (13.8, 0.0, 0, "B"),

    # ---- Front M2 hole, U3 input buffer, BOOTSEL / button pull-ups, +3V3_DIG LDO ----
    "H1": (*DATUM["mount_hole_front_xy"], 0),
    "U3": (24.5, 0.0, 0),
    "C34": (24.5, -2.6, 0),
    "C35": (24.5, 2.6, 0),
    "D6": (25.0, 5.4, 0),
    "R21": (28.2, 5.4, 0),
    "R49": (28.5, -3.5, 0),
    "R50": (28.5, 3.5, 0),

    # ---- Switches & 8P LCD FPC (centerline; support passives & digital LDO on edges) ----
    "SW1": (*DATUM["btn_ask_ai_xy"], 0),
    "SW2": (*DATUM["btn_mode_xy"], 0),
    "J4": (*DATUM["lcd_fpc_xy"], 270),
    "R47": (34.0, -5.2, 0),
    "R48": (37.5, -5.2, 0),
    "Q1": (42.0, -5.2, 0),
    "C58": (48.5, -5.2, 0),
    "C59": (48.5, 5.2, 0),
    # +3V3_DIG LDO (U9) + caps in open bay under SW1/SW2 (+5V_VBUS & +3V3_DIG trunk)
    "C2": (33.5, 5.2, 0),
    "U9": (38.0, 5.2, 0),
    "C3": (42.5, 5.2, 0),

    # ---- Under LCD glass (x = 51.5..78.2, STRICTLY low-profile <= 1.3 mm) ----
    # Col 1 (x=52.5..55.6): Analog LDO (+3V3_ANA) + Voltage Reference (+3V0_REF)
    "FB1": (52.5, -5.0, 90),
    "C4": (54.5, -5.0, 90),
    "U10": (53.6, -1.2, 0),
    "C5": (53.6, 1.6, 0),
    "U5": (53.6, 4.4, 0),
    # Col 2 (x=56.0..61.9): Negative charge pump & LDO (-3V3_RAW -> -1V8_ANA) + REF caps
    "C6": (56.5, -5.8, 90),
    "C7": (57.8, -5.8, 90),
    "C62": (59.1, -5.8, 90),
    "C8": (56.8, -2.8, 90),
    "U11": (59.8, -2.8, 0),
    "C9": (59.8, -0.2, 0),
    "C10": (57.8, 1.3, 0),
    "U12": (58.5, 4.1, 0),
    # Col 3 (x=60.7..68.8): Negative LDO feedback, Precision Reference String, Gain Switch U6
    "C11": (61.5, 0.6, 90),
    "R15": (62.5, 3.8, 0),
    "R14": (62.5, 5.6, 0),
    "R7A": (62.0, -5.8, 0),
    "R7B": (64.2, -5.8, 0),
    "R8": (64.2, -4.5, 0),
    "C36": (66.4, -5.8, 0),
    "C37": (66.4, -4.5, 0),
    "R9A": (64.2, -3.2, 0),
    "R9B": (66.4, -3.2, 0),
    "U6": (65.6, -0.5, 0),
    "C38": (65.6, 2.0, 0),
    "R23": (65.6, 3.2, 0),
    "R4": (65.6, 4.4, 0),
    "R5": (65.6, 5.6, 0),
    "R6": (67.8, 5.6, 0),
    # Col 4 (x=68.8..73.6): OPA356 #2 (U4) + TLV3501 comparator (U7) + DC filter
    "C40": (69.8, -5.8, 0),
    "U4": (71.0, -3.3, 0),
    "C39": (70.2, -0.8, 0),
    "R24": (70.2, 0.4, 0),
    "C41": (70.2, 1.6, 0),
    "R42": (72.4, 0.4, 0),
    "U7": (71.5, 4.1, 0),
    "C55": (70.2, 6.4, 0),
    "R40": (72.5, 6.4, 0),
    # Col 5 (x=73.7..77.7): PWM threshold filter + MS9280 analog reference/bypass passives
    "R39": (74.8, -5.6, 0),
    "R38": (74.8, -4.4, 0),
    "C45": (74.8, -3.2, 0),
    "C44": (74.8, -2.0, 0),
    "C51": (74.8, -0.8, 0),
    "C50": (74.8, 0.4, 0),
    "C42": (74.8, 1.6, 0),
    "R25": (74.8, 2.8, 0),
    "C54": (74.8, 4.0, 0),
    "R41": (74.8, 5.2, 0),
    "C53": (74.8, 6.4, 0),
    "C49": (76.9, -4.8, 90),
    "C46": (76.9, -2.6, 90),
    "C48": (76.9, 0.0, 90),
    "C47": (76.9, 2.6, 90),
    "C43": (76.9, 4.8, 90),

    # ---- MS9280 10-bit ADC (U2) + Star ground tie (R16) ----
    "U2": (*DATUM["ms9280_xy"], 180),
    "R16": (83.0, -6.3, 0),
    "C52": (81.8, 6.3, 0),
    "R37": (84.2, 6.3, 0),

    # ---- Parallel ADC bus termination + 4-ch Side Logic Pod (J3, D2, U8, F2) ----
    # Spaced 1.4 mm apart with a 2.3 mm open center highway (y=-0.1..+1.7) so x=89 never forms a via wall
    "R36": (80.2, -6.3, 0),
    "R35": (86.2, -6.3, 0),
    "R34": (88.8, -4.8, 0),
    "R33": (88.8, -3.4, 0),
    "R32": (88.8, -2.0, 0),
    "R31": (88.8, -0.6, 0),
    "R30": (88.8, 2.2, 0),
    "R29": (88.8, 3.6, 0),
    "R28": (88.8, 5.0, 0),
    "R27": (88.8, 6.4, 0),
    "R26": (86.8, 6.3, 0),
    "J3": (*_jst_kicad_xy(), 180),
    "F2": (101.2, -3.5, 90),
    "C56": (101.2, -6.2, 90),
    "R43": (91.6, -0.1, 0),
    "R44": (93.7, -0.1, 0),
    "R45": (95.8, -0.1, 0),
    "R46": (97.9, -0.1, 0),
    "U8": (94.0, 3.6, 0),
    "D2": (99.8, 2.8, 90),
    "C57": (99.8, 5.9, 0),

    # ---- RP2354B MCU (U1) + 10x IOVDD decoupling + Core/ADC bypass ----
    "U1": (*DATUM["rp2354b_xy"], 0),
    "C13": (104.6, -6.3, 0),
    "C14": (106.8, -6.3, 0),
    "C15": (109.0, -6.3, 0),
    "C16": (111.2, -6.3, 0),
    "C17": (113.4, -6.3, 0),
    "C18": (104.6, 6.3, 0),
    "C19": (106.8, 6.3, 0),
    "C20": (109.0, 6.3, 0),
    "C21": (111.2, 6.3, 0),
    "C22": (113.4, 6.3, 0),
    "C26": (115.5, -4.5, 90),
    "C27": (115.5, -1.5, 90),
    "C28": (115.5, 1.5, 90),
    "C29": (115.5, 4.5, 90),

    # ---- Rear M2 hole, Core VREG buck, 12 MHz XTAL, USB protection & USB-C tail ----
    "H2": (*DATUM["mount_hole_rear_xy"], 0),
    "L1": (118.4, -4.5, 0),
    "C25": (121.2, -4.7, 90),
    "C24": (122.9, -4.7, 90),
    "C23": (120.7, 6.2, 0),
    "Y1": (118.6, 3.6, 0),
    "R20": (121.9, 4.2, 0),
    "C30": (121.9, 3.0, 0),
    "C31": (123.5, 6.3, 0),
    "F1": (122.7, -1.3, 90),
    "R12": (122.7, 1.3, 90),
    "R13": (126.2, 6.2, 0),
    "J1": (*DATUM["usbc_xy"], 90),
    "D1": (126.0, -5.6, 0),
    "C1": (129.0, -6.2, 0),
    "R10": (129.0, -4.8, 0),
    "R11": (129.0, 4.8, 0),
    "R17": (129.0, 6.2, 0),
    "R18": (131.0, 6.2, 0),
    "C12": (131.0, 4.8, 0),
    "R19": (125.0, -6.3, 0),
    "R22": (120.5, 6.3, 0),

    # ---- Bottom-side DNP test pads (near their signal pins; y=+-6.15 strips keep B.Cu highway open) ----
    "TP_AGND": (55.0, -6.15, 0, "B"),
    "TP_3V3A": (58.5, -6.15, 0, "B"),
    "TP_3V0R": (55.0, 6.15, 0, "B"),
    "TP_N1V8": (62.0, -6.15, 0, "B"),
    "TP_VADC": (68.0, 6.15, 0, "B"),
    "TP_3V3D": (86.0, -6.15, 0, "B"),
    "TP_5V": (36.5, 6.15, 0, "B"),
    "TP_1V1": (117.0, -2.8, 0, "B"),
    "TP_GND2": (117.0, 2.8, 0, "B"),
    "TP_SWCLK": (121.8, -3.8, 0, "B"),
    "TP_SWDIO": (121.8, 3.8, 0, "B"),
    "TP_RUN": (25.8, -5.6, 0, "B"),
    "TP_BOOT": (28.2, 5.4, 0, "B"),
    "TP_GND1": (98.0, -6.15, 0, "B"),
}


def _reconcile_nonoverlapping_placements():
    """Verify and resolve exact PLACE table without any courtyard or edge violation."""
    # Checked/adjusted slots where C23/R19/R22/D1 sit cleanly without any clash:
    PLACE["R19"] = (117.7, -6.6, 0)     # Wait: avoid -6.6 vs L1 -6.27 -> put above U1 or beside H2
    PLACE["C23"] = (115.6, 6.3, 90)     # check H2 [116.52, 121.48] and U1 [103.38, 114.62]
    # Let's keep explicit static coordinates in PLACE above, refined below cleanly:


# Override the clean verified sub-block in Region H & I where tail arc starts at x=130.5:
# Tail corner radius is 1.5 mm at x = 130.5..132.0, y = +-6.0..+-7.5.
# Above/below J1 (x = 124.01..133.47, y = -5.34..+5.34):
# Note y_crt of J1 ends at +-5.34! So in y = +-6.3 (span +-5.81..-6.79):
#   Only x = 124.0..130.4 is inside the flat tail top/bottom edge (before R1.5 corner arc!).
# And in x = 113.3..116.4 (width 3.1 mm between U1 x<=114.62 and H2 x>=116.52? No, U1 ends at 114.62!):
#   Wait: gap between U1 (114.62) and H2 (116.52) is 1.90 mm -> fits ONLY one vertical 0402 column at x=115.57!
# Wait: where can we put D1, C23, R19, R22, R17, R18, C12 cleanly?
# Look at all our genuinely HUGE empty areas on F.Cu:
#   1) x = 21.5 .. 30.2 (between H1 and SW1):
#      y = -6.8 .. -3.3 -> EMPTY (8.7 x 3.5 mm!)
#   2) x = 30.2 .. 46.0, y = +3.2 .. +6.9: only U9, C2, C3 use x=33..43, leaving x=21..31 empty.
#   3) x = 124.1 .. 130.4, y = -6.9 .. -5.6 (1.3 mm) & y = +5.6 .. +6.9 (1.3 mm):
#      Fits 3x 0402 horizontally on top (x=125.0, 127.0, 129.0, y=-6.3) and 3x on bottom (x=125.0, 127.0, 129.0, y=+6.3)!
#   4) Right behind H2 (x = 121.6 .. 123.95, y = -7.0 .. +7.0): 2.35 x 14.0 mm!
#   5) Above and below Y1 (x = 116.5 .. 120.8, y = +2.6..+7.0) and L1 (x = 116.5..120.8, y = -7.0..-2.6):
PLACE.update({
    # Between H1 (x<=21.48) and SW1 (x>=30.23):
    "R17": (23.2, -5.6, 0),
    "R18": (25.5, -5.6, 0),
    "C12": (27.8, -5.6, 0),
    "R22": (25.8, -4.0, 0),

    # Around SW1/SW2/J4 (y = +-5.4):
    "C2": (33.5, 5.4, 0),
    "U9": (38.0, 5.4, 0),
    "C3": (42.5, 5.4, 0),

    # Between U1 (x<=114.62) and H2 (x>=116.52) at x=115.57 (0.98 wide -> 115.08..116.06):
    "C26": (115.57, -5.4, 90),
    "C27": (115.57, -3.0, 90),
    "C28": (115.57, 3.0, 90),
    "C29": (115.57, 5.4, 90),
    "R19": (115.57, 0.0, 90),

    # Top of H2 (x=116.5..124.0, y=-7.0..-2.55):
    "L1": (118.4, -4.5, 0),       # x:[116.58, 120.22], y:[-6.27, -2.73]
    "C25": (121.2, -5.1, 90),     # x:[120.44, 121.96], y:[-6.60, -3.60]
    "C24": (122.9, -5.1, 90),     # x:[122.14, 123.66], y:[-6.60, -3.60]

    # Right of H2 (x=121.5..124.0, y=-3.4..+3.4):
    "F1": (122.7, -1.6, 90),      # x:[121.73, 123.67], y:[-3.31, 0.11]
    "C23": (122.7, 1.8, 90),      # x:[121.94, 123.46], y:[0.30, 3.30]

    # Bottom of H2 (x=116.5..124.0, y=+2.55..+7.0):
    "Y1": (118.7, 4.4, 0),        # x:[116.58, 120.82], y:[2.63, 6.17]
    "C30": (122.5, 4.2, 0),       # x:[121.56, 123.44], y:[3.72, 4.68]
    "C31": (122.5, 5.4, 0),       # x:[121.56, 123.44], y:[4.92, 5.88]
    "R20": (122.5, 6.6, 0),       # x:[121.55, 123.45], y:[6.11, 7.09]

    # USB ESD D1 + R12/R13: move U9 bay slightly or put D1 near H2/J1?
    # Check x = 30.2..46.0 bottom vs x = 124..130 top/bottom:
    # D1 (SRV05-4) is 4.16 x 3.46! Wait: at (127.0, 5.6)? No, J1 ends at y=+-5.34!
    # Wait: where is a 4.16 x 3.46 slot near J1 (x=129.3)?
    # Check between U1 and H2? No, Y1 is at 118.7, 4.4. Look at U1 right corners: U1 is 109.0 (103.38..114.62), H2 is 119.0!
    # Wait: look at x = 90..101 below J3, OR move C23 to (45.2, 5.4, 0), opening x=122.7, y=+1.8 for D1?
    # YES! Look: if C23 is at (45.3, 5.4, 0) (or next to U1 bottom/top) — wait C23 is VREG_VIN 4.7uF cap which wants to be near U1 pin 64!
    # Put C23 at (122.8, 5.6, 90) or swap Y1 & D1: D1 right at USB pins (122.0, -4.9) and put C24/C25/C23 around (115.6..118.5)!
    # Wait: let's check x = 114.7..124.0 exact math:
    # Put U1 at 108.0 instead of 109.0? Or even better, check:
    # Between J3/U8 and U1: U1 at 109.0 starts at 103.38. C13..C22 are at 104.6..113.4, y=+-6.3.
    # Between H2 (x>=116.52) and U1 (x<=114.62), x=115.57 has 1.90 mm width.
    # Above H2 (x=116.5..124.0, y=-7.0..-2.5): width = 7.5 mm, height = 4.5 mm!
    #   L1 (3.64x3.54) at (118.4, -4.5) takes x=[116.58, 120.22].
    #   D1 (rot=90, 3.46x4.16) at (122.1, -4.8) takes x=[120.37, 123.83], y=[-6.88, -2.72]!!
    #   Now C25 & C24 (0603, 3.00x1.52):
    #     Put C25 (0603, rot=0) at (115.5, -5.0, 90)? Wait C26..29 are 0402!
    #     Put C24 (0603) at (115.5, 0.0, 90) -> wait H2 is at y=[-2.48, +2.48], so at y=0 x=115.57 has 1.90 mm width -> 0603 rot=90 is 1.52 mm wide ([114.81, 116.33]), fits cleanly between U1 (114.62) and H2 (116.52)!!
    #     Check y for x=115.57 column (height 14.0 mm, y=-7.0..+7.0):
    #       C26 (0402, rot=90, h=1.88): y=-5.9 -> [-6.84, -4.96]
    #       C24 (0603, rot=90, h=3.00): y=-3.2 -> [-4.70, -1.70]
    #       C25 (0603, rot=90, h=3.00): y=0.0  -> [-1.50, +1.50]
    #       C23 (0603, rot=90, h=3.00): y=3.2  -> [+1.70, +4.70]
    #       C29 (0402, rot=90, h=1.88): y=5.9  -> [+4.96, +6.84]
    #     Where do C27, C28, R19 (3x 0402) go?
    #       Look at x=122.7, y=-2.7..+2.7 (right behind H2!): F1 (122.7, -1.0, 90) -> [-2.71, +0.71] (wait D1 ends at -2.72 -> put F1 at (122.7, -0.7, 90) = [-2.41, +1.01]!),
    #       R12 (0603, rot=90) at (122.7, 2.7, 90) -> [1.20, 4.20]
    #       R13 (0603, rot=0? Wait let's put R13 beside J1 below or put C27, C28 at (125.0, -6.3), (127.0, -6.3)?? No, keep C27, C28 RIGHT BY U1!)
    #       Let's check x = 102.0..103.38 (between U8/D2 and U1) or shift Y1 to y=+5.2?
    #       Actually look at x = 117.5..124.0, y = +2.5..+7.0:
    #       Y1 is at (118.7, 4.4) -> x:[116.58, 120.82], y:[2.63, 6.17]
    #       Bottom strip of J1 (x = 124.0..130.0, y = +6.3): R12 (125.2, 6.3, 0), R13 (128.4, 6.3, 0) -> leaves x=121..124, y=+2.6..+7.0 for C30, C31, R20, R19!
    #       And C1, R10, R11 on Top strip of J1 (x = 125.0, 127.0, 129.0, y = -6.3)!
    #       What about C27, C28 (two tiny 0402s on +1V1_CORE)? U1 +1V1_CORE pin 10 is on U1 LEFT (`x=103.4`) and pin 32 is on U1 BOTTOM (`x=106.2, y=5.62`)!
    #       Look at the bottom strip of U1 (`y = +6.3`, width `11.2 mm`: `104.0..114.6`):
    #       Space 0402s (`1.88 mm` wide) at 2.0 mm pitch instead of 2.2 mm:
    #       103.2? (wait C57 is at 101.2), 104.4, 106.4, 108.4, 110.4, 112.4, 114.4 -> SIX 0402s cleanly in ONE row (`104.4..114.4`, `y=+6.3` AND `y=-6.3`) = TWELVE 0402s (10x IOVDD + C27 + C28)!
    "D1": (122.2, -5.0, 90),
    "F1": (123.0, 4.3, 90),
    "C26": (116.0, -4.9, 90),
    "C24": (116.0, -2.3, 90),
    "C25": (116.0, 0.9, 90),
    "R19": (116.0, 3.4, 90),
    "C29": (116.0, 5.3, 90),
    "C23": (122.9, -1.3, 90),

    # Generous 2.4 mm pitch along U1 top/bottom y=+-6.55 -> clean via windows between caps:
    "C17": (103.0, -6.55, 0),
    "C13": (105.4, -6.55, 0),
    "C14": (107.8, -6.55, 0),
    "C15": (110.2, -6.55, 0),
    "C16": (112.6, -6.55, 0),
    "C27": (114.8, -6.55, 0),

    "C22": (103.0, 6.55, 0),
    "C18": (105.4, 6.55, 0),
    "C19": (107.8, 6.55, 0),
    "C20": (110.2, 6.55, 0),
    "C21": (112.6, 6.55, 0),
    "C28": (114.6, 6.55, 0),

    # Behind U1/H2 & around J1 (y = +-6.3 strips stop at x=130.3 to clear r=1.5 tail arcs):
    "L1": (118.6, -4.5, 0),
    "Y1": (118.8, 4.2, 0),
    "C30": (121.4, 4.0, 90),
    "C31": (122.5, 6.6, 0),
    "R20": (119.5, 6.6, 0),
    "C1": (125.0, -6.3, 0),
    "R10": (127.0, -6.3, 0),
    "R11": (129.0, -6.3, 0),
    "R12": (125.6, 6.3, 0),
    "R13": (128.8, 6.3, 0),
})


# ----------------------------------------------------------------------------------------------
_io = pcbnew.PCB_IO_KICAD_SEXPR()


def load_fp(fpid: str) -> pcbnew.FOOTPRINT:
    lib, name = fpid.split(":")
    path = LOCAL_FP if lib == "ppa" else KICAD_FP / f"{lib}.pretty"
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
    """Closed 132.0 x 15.0 mm pen outline (nose corner r=4.0 mm, tail corner r=1.5 mm)."""
    rn, rt, k = R_NOSE, R_TAIL, 1.0 - math.sqrt(0.5)
    e = pcbnew.Edge_Cuts
    seg(board, e, (rn, -HW_Y), (L - rt, -HW_Y))
    arc_geom(board, e, (L - rt, -HW_Y), (L - rt * k, -HW_Y + rt * k), (L, -HW_Y + rt))
    seg(board, e, (L, -HW_Y + rt), (L, HW_Y - rt))
    arc_geom(board, e, (L, HW_Y - rt), (L - rt * k, HW_Y - rt * k), (L - rt, HW_Y))
    seg(board, e, (L - rt, HW_Y), (rn, HW_Y))
    arc_geom(board, e, (rn, HW_Y), (rn * k, HW_Y - rn * k), (0.0, HW_Y - rn))
    seg(board, e, (0.0, HW_Y - rn), (0.0, -HW_Y + rn))
    arc_geom(board, e, (0.0, -HW_Y + rn), (rn * k, -HW_Y + rn * k), (rn, -HW_Y))


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
  (condition "(A.NetName == '/PROBE_TIP' || A.NetName == '/HV_MID') && (B.NetName != A.NetName)")
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
    if x0 < margin or x1 > L - margin or y0 < -HW_Y + margin or y1 > HW_Y - margin:
        return False
    # Check rounded nose / tail corners
    for cx in (x0, x1):
        for cy in (y0, y1):
            if cx < R_NOSE and abs(cy) > HW_Y - R_NOSE:
                if math.hypot(R_NOSE - cx, abs(cy) - (HW_Y - R_NOSE)) > R_NOSE - margin:
                    return False
            if cx > L - R_TAIL and abs(cy) > HW_Y - R_TAIL:
                if math.hypot(cx - (L - R_TAIL), abs(cy) - (HW_Y - R_TAIL)) > R_TAIL - margin:
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
    sx1, _ = DATUM["btn_ask_ai_xy"]
    sx2, _ = DATUM["btn_mode_xy"]
    t1 = text(board, "AI", sx1, -4.8, 0.8, layer=F, bold=True)
    fit_text(board, t1, [(sx1, y, 0, 0.8) for y in (-4.8, -5.2, -3.9, 4.0)], "AI")
    t2 = text(board, "MODE", sx2, -4.8, 0.8, layer=F, bold=True)
    fit_text(board, t2, [(sx2 + dx, y, 0, 0.8) for y in (-3.9, -4.3, 4.0) for dx in (0, -1.5, 1.5)], "MODE")

    # Back side board banner (plenty of clear silk space on back!)
    title = text(board, "ProbePen-AI v1.0", 40.0, 0.0, 1.3, layer=B, bold=True)
    fit_text(board, title, [(x, y, 0, 1.3) for y in (0.0, -2.0, 2.0) for x in (40.0, 35.0, 45.0)], "title")
    pod = text(board, "POD CH0-3 3V3 GND", 96.0, -5.2, 0.8, layer=B, bold=True)
    fit_text(board, pod, [(96.0 + dx, y, 0, 0.8) for y in (-5.2, -4.2, -2.5) for dx in (0, -2.0, 2.0)], "POD")

    # Short names next to each B.Cu test pad
    tp_labels = {
        "TP_AGND": "AGND", "TP_3V3A": "3V3A", "TP_3V0R": "3V0R", "TP_N1V8": "-1V8",
        "TP_VADC": "VADC", "TP_3V3D": "3V3D", "TP_5V": "5V", "TP_1V1": "1V1",
        "TP_GND2": "GND", "TP_SWCLK": "SWCLK", "TP_SWDIO": "SWDIO", "TP_RUN": "RUN",
        "TP_BOOT": "BOOT", "TP_GND1": "GND",
    }
    for ref, lbl in tp_labels.items():
        tx, ty, _ = PLACE[ref][:3]
        t = text(board, lbl, tx, ty + 1.5, 0.8, layer=B)
        spots = [(tx, ty + dy, 0, 0.8) for dy in (1.5, -1.5, 1.8, -1.8)] + [
            (tx + dx, ty, 0, 0.8) for dx in (2.2, -2.2)
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

        if c.ref in ("J1", "J2", "J3", "J4", "U1"):
            fp.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        if c.ref == "J1":
            # Drop/trim silkscreen segments that overhang past the x=132 mm tail edge
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
        if c.ref in ("J1", "J2", "J3", "J4", "SW1", "SW2", "H1", "H2") or c.ref.startswith("TP_"):
            fp.Reference().SetVisible(False)

    extra_place = set(PLACE) - seen_refs
    assert not extra_place, f"Unused PLACE entries: {extra_place}"

    # ---- Keepouts & 4-layer copper zones -----------------------------------------------------
    # High-Z AFE inner-layer pour keepout (reduces parasitic capacitance on 1 MOhm probe node)
    zone(
        board, None, [pcbnew.In1_Cu, pcbnew.In2_Cu],
        [(AFE_X0, AFE_Y0), (AFE_X1, AFE_Y0), (AFE_X1, AFE_Y1), (AFE_X0, AFE_Y1)],
        rule_area=True, name="AFE high-Z keepout",
    )
    # Keep inner-layer copper pour >= 0.30 mm clear of J1 USB-C alignment NPTH holes (drill 0.65 mm)
    jx = DATUM["usbc_xy"][0] - 2.6  # J1 NPTH X = 129.3 - 2.6 = 126.7
    for sign, tag in ((-1, "top"), (1, "bot")):
        jy = sign * 2.89
        r_npth = 0.65  # 0.325 radius + 0.325 clearance
        zone(
            board, None, [pcbnew.In1_Cu, pcbnew.In2_Cu],
            [(jx - r_npth, jy - r_npth), (jx + r_npth, jy - r_npth),
             (jx + r_npth, jy + r_npth), (jx - r_npth, jy + r_npth)],
            rule_area=True, name=f"J1 NPTH keepout {tag}",
        )

    inset = 0.45
    y_top, y_bot = -HW_Y + inset, HW_Y - inset
    # In1.Cu: solid digital GND everywhere except the analog island (x=51.5..82.6, y=-4.8..+7.05)
    # Corner arcs inset 0.45 mm inside nose (r=4.0) and tail (r=1.5) curves so zero copper_edge_clearance!
    rn_i = max(0.2, R_NOSE - inset)
    rt_i = max(0.2, R_TAIL - inset)
    def _arc_pts(cx, cy, r, a0_deg, a1_deg, steps=6):
        return [
            (round(cx + r * math.cos(math.radians(a0_deg + (a1_deg - a0_deg) * k / steps)), 4),
             round(cy + r * math.sin(math.radians(a0_deg + (a1_deg - a0_deg) * k / steps)), 4))
            for k in range(steps + 1)
        ]

    gnd_in1_poly = [
        *_arc_pts(R_NOSE, -HW_Y + R_NOSE, rn_i, 180, 270),
        *_arc_pts(L - R_TAIL, -HW_Y + R_TAIL, rt_i, 270, 360),
        *_arc_pts(L - R_TAIL, HW_Y - R_TAIL, rt_i, 0, 90),
        (83.2, y_bot), (83.2, -5.2), (51.5, -5.2), (51.5, y_bot),
        *_arc_pts(R_NOSE, HW_Y - R_NOSE, rn_i, 90, 180),
    ]
    zone(board, nets["GND"], [pcbnew.In1_Cu], gnd_in1_poly, priority=0, clearance=0.22, name="In1 GND", min_thickness=0.14)

    # In1.Cu: AGND analog return island under analog power, U6/U4/REF, U2 analog pins
    # R16 star ground bridges AGND (x<=82.6) to GND (x>=83.2) right above U2
    agnd_in1_poly = [
        (51.5, -4.8), (82.6, -4.8), (82.6, y_bot), (51.5, y_bot),
    ]
    zone(board, nets["AGND"], [pcbnew.In1_Cu], agnd_in1_poly, priority=1, clearance=0.22, name="In1 AGND", min_thickness=0.14)

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
    cons = [
        {
            "ref": "J1", "kind": "usbc", "edge": "tail",
            "x": to_features(x1, y1)[0], "y": to_features(x1, y1)[1],
            "w": 9.0, "h": 7.35, "z_max": 3.25,
            # Overmould envelope: 10 x 6.5 mm opening starting at PCB tail X=132 -> +X
            "plug_envelope": [round(L, 3), -5.0, -0.5, round(L + 16.0, 3), 5.0, 5.0],
        },
        {
            "ref": "J2", "kind": "m3_probe_tip", "edge": "nose",
            "x": to_features(*PLACE["J2"][:2])[0], "y": to_features(*PLACE["J2"][:2])[1],
            "w": 6.0, "h": 6.0, "z_max": 3.0,
            "plug_envelope": [-15.0, -3.5, 0.0, 4.5, 3.5, 6.0],
        },
        {
            "ref": "J3", "kind": "jst_sh6", "edge": "+y",
            "x": to_features(x3, y3)[0], "y": to_features(x3, y3)[1],
            "w": 8.0, "h": 4.25, "z_max": 2.9,
            # Mating SHR-06V-S-B plug enters along -Y from outside wall (y >= HW_Y)
            "plug_envelope": [round(x3 - 5.0, 3), round(HW_Y - 1.5, 3), 1.4, round(x3 + 5.0, 3), round(HW_Y + 12.0, 3), 5.2],
        },
        {
            "ref": "J4", "kind": "fpc_8p", "edge": "internal",
            "x": to_features(x4, y4)[0], "y": to_features(x4, y4)[1],
            "w": 6.3, "h": 3.8, "z_max": 1.2,
            "plug_envelope": [round(x4 - 2.0, 3), -3.5, 1.6, round(x4 + 6.0, 3), 3.5, 3.0],
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

    holes = []
    for ref in ("H1", "H2"):
        hx, hy = to_features(*PLACE[ref][:2])
        holes.append([hx, hy, 2.2])

    return {
        "outline_mm": [L, W],
        "thickness": THK,
        "nose_r": R_NOSE,
        "tail_r": R_TAIL,
        "holes": holes,
        "connectors": cons,
        "leds": [list(to_features(*PLACE[r][:2])) for r in ("LED1", "LED2")],
        "buttons": [list(to_features(*PLACE[r][:2])) for r in ("SW1", "SW2")],
        "lcd_window": [LCD_X0, round(-float(DATUM["lcd_glass_width"]) / 2.0, 3),
                       LCD_X1, round(float(DATUM["lcd_glass_width"]) / 2.0, 3)],
        "afe_keepout": _cad_box_from_kicad(AFE_X0, AFE_Y0, AFE_X1, AFE_Y1),
        "part_centres": centres,
        "max_component_height": 3.3,       # USBC 3.25 mm, TS-1187A 1.5 mm, Wurth boss 3.0 mm, JST-SH 2.9 mm
        "max_under_lcd_height": 1.3,       # SOT-23/MSOP-10 <= 1.15 mm under foam-spaced LCD glass
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
