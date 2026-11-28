"""3D models (STEP, with colours) for every footprint on the board, built with build123d.

KiCad's library 3D models are not installed here, so each part is modelled from its real pads
(hw/out/fp_geometry.json, written by hw/tools/dump_fp_geometry.py) and datasheet body sizes. The
RP2354B, USB-C and inductor keep the vendor models in hw/lib/ppa.3dshapes. hw/gen_pcb.py points each
footprint at hw/lib/ppa.3dshapes/gen/<footprint>.step.

    kicad-python hw/tools/dump_fp_geometry.py && .venv/bin/python -m hw.gen_3d

Model frame (KiCad): origin at the footprint origin on the board surface, Z up, Y = -(footprint y).
"""
from __future__ import annotations

import json
from pathlib import Path

from build123d import Box, Color, Compound, Cylinder, Location, export_step

ROOT = Path(__file__).resolve().parents[1]
GEOM = ROOT / "hw" / "out" / "fp_geometry.json"
OUT = ROOT / "hw" / "lib" / "ppa.3dshapes" / "gen"

BLACK = (0.10, 0.10, 0.11)
TIN = (0.80, 0.80, 0.82)
TAN = (0.76, 0.62, 0.42)
WHITE = (0.93, 0.93, 0.91)
GREY = (0.55, 0.56, 0.58)
SILVER = (0.82, 0.83, 0.86)
BEIGE = (0.95, 0.91, 0.80)
ORANGE = (0.96, 0.50, 0.10)
AMBER = (0.86, 0.56, 0.16)
GLASS = (0.04, 0.05, 0.07)
SCREEN = (0.10, 0.20, 0.42)
YELLOW = (1.00, 0.88, 0.35)
GREEN = (0.45, 0.60, 0.42)


def fbox(x0, y0, x1, y1, z0, z1, rgb, label="body"):
    """Box between footprint-frame corners (y flipped into the model frame)."""
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((-y0, -y1))
    b = Location(((xa + xb) / 2, (ya + yb) / 2, (z0 + z1) / 2)) * Box(xb - xa, yb - ya, z1 - z0)
    b.color = Color(*rgb)
    b.label = label
    return b


def fcyl(x, y, z0, z1, d, rgb, label="body"):
    c = Location((x, -y, (z0 + z1) / 2)) * Cylinder(d / 2, z1 - z0)
    c.color = Color(*rgb)
    c.label = label
    return c


def save(name: str, parts: list) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.step"
    export_step(Compound(children=parts, label=name), str(path))
    return path


# ---- generic packages --------------------------------------------------------------------------

CHIP = {  # name fragment -> (length, width)
    "0402": (1.0, 0.5), "0603": (1.6, 0.8), "0805": (2.0, 1.25),
}
CHIP_H = {"R": {"0402": 0.35, "0603": 0.45, "0805": 0.5}, "C": {"0402": 0.5, "0603": 0.8, "0805": 0.85},
          "L": {"0603": 0.8}, "F": {"0805": 0.85}}
CHIP_COL = {"R": BLACK, "C": TAN, "L": GREY, "F": GREEN}


def chip(kind: str, size: str):
    L, W = CHIP[size]
    H = CHIP_H[kind][size]
    t = {"0402": 0.2, "0603": 0.3, "0805": 0.4}[size]
    return [
        fbox(-L / 2 + t, -W / 2, L / 2 - t, W / 2, 0.0, H, CHIP_COL[kind]),
        fbox(-L / 2, -W / 2, -L / 2 + t, W / 2, 0.0, H, TIN, "term"),
        fbox(L / 2 - t, -W / 2, L / 2, W / 2, 0.0, H, TIN, "term"),
    ]


def gullwing(pads, bx, by, h, pin1=True, band_x=None):
    """Plastic body +-bx, +-by (footprint frame) and a flat lead from the body edge out to each pad."""
    parts = [fbox(-bx, -by, bx, by, 0.1, h, BLACK)]
    for p in pads:
        x, y, w, hh = p["x"], p["y"], p["w"], p["h"]
        if abs(x) > bx:                                     # lead leaves through a +-x side
            s = 1 if x > 0 else -1
            end = x + s * w * 0.35
            lw = min(hh * 0.65, 0.4)
            parts.append(fbox(s * bx, y - lw / 2, end, y + lw / 2, 0.0, 0.15, TIN, "lead"))
            parts.append(fbox(s * bx, y - lw / 2, s * (bx + 0.12), y + lw / 2, 0.15, 0.45, TIN, "lead"))
        else:                                               # through a +-y side
            s = 1 if y > 0 else -1
            end = y + s * hh * 0.35
            lw = min(w * 0.65, 0.4)
            parts.append(fbox(x - lw / 2, s * by, x + lw / 2, end, 0.0, 0.15, TIN, "lead"))
            parts.append(fbox(x - lw / 2, s * by, x + lw / 2, s * (by + 0.12), 0.15, 0.45, TIN, "lead"))
    if pin1:
        p1 = next((p for p in pads if p["num"] == "1"), pads[0])
        dx = 0.6 * bx * (1 if p1["x"] > 0 else -1) if abs(p1["x"]) > 1e-6 else 0.0
        dy = 0.6 * by * (1 if p1["y"] > 0 else -1) if abs(p1["y"]) > 1e-6 else 0.0
        parts.append(fcyl(dx, dy, h, h + 0.02, min(bx, by) * 0.35, WHITE, "pin1"))
    if band_x is not None:                                  # diode cathode band
        parts.append(fbox(band_x - 0.15, -by, band_x + 0.15, by, h, h + 0.02, GREY, "band"))
    return parts


# ---- specific parts ----------------------------------------------------------------------------

def crystal_2520():
    return [fbox(-1.25, -1.0, 1.25, 1.0, 0.0, 0.25, TAN), fbox(-1.1, -0.85, 1.1, 0.85, 0.25, 0.55, SILVER, "lid")]


def crystal_3225():
    return [fbox(-1.6, -1.25, 1.6, 1.25, 0.0, 0.25, TAN), fbox(-1.45, -1.1, 1.45, 1.1, 0.25, 0.75, SILVER, "lid")]


def jst_sh6_horizontal(pads):
    parts = [fbox(-4.0, -1.7, 4.0, 2.6, 0.0, 2.95, BEIGE)]
    parts.append(fbox(-3.0, 0.4, 3.0, 2.62, 0.55, 2.45, (0.35, 0.33, 0.30), "socket"))   # mating opening, +y side
    for p in pads:
        if p["num"] == "MP":
            parts.append(fbox(p["x"] - 0.5, p["y"] - 0.8, p["x"] + 0.5, p["y"] + 0.8, 0.0, 0.2, TIN, "tab"))
            parts.append(fbox(p["x"] - 0.5, p["y"] - 0.8, p["x"] + 0.5, p["y"] - 0.65, 0.2, 2.2, TIN, "tab"))
        else:
            parts.append(fbox(p["x"] - 0.15, -2.55, p["x"] + 0.15, -1.7, 0.0, 0.15, TIN, "lead"))
    return parts


def ts24ca(pads):
    parts = [fbox(-2.275, -1.225, 2.275, 1.075, 0.0, 1.75, BLACK),
             fbox(-2.275, -1.225, 2.275, 1.075, 1.75, 1.9, SILVER, "shield"),
             fbox(-1.0, -2.425, 1.0, -1.225, 0.45, 1.45, (0.85, 0.85, 0.85), "plunger")]
    for p in pads:
        parts.append(fbox(p["x"] - p["w"] * 0.35, p["y"] - p["h"] * 0.35, p["x"] + p["w"] * 0.35, p["y"] + p["h"] * 0.35,
                          0.0, 0.12, TIN, "term"))
    return parts


def led_xl3806(pads):
    parts = [fbox(-1.9, -0.5, 1.9, 0.5, 0.0, 0.6, WHITE),
             fbox(-1.3, -0.52, 1.3, -0.40, 0.08, 0.52, YELLOW, "lens")]           # emits towards -y
    for p in pads:
        parts.append(fbox(p["x"] - 0.35, -0.5, p["x"] + 0.35, 0.5, 0.0, 0.1, TIN, "term"))
    return parts


def jz100():
    parts = [fbox(-2.25, -1.6, 2.25, 1.6, 0.0, 1.0, WHITE)]
    parts.append(fcyl(0.0, 0.0, 1.0, 1.3, 2.6, SILVER, "rotor"))
    parts.append(fbox(-1.0, -0.15, 1.0, 0.15, 1.3, 1.32, BLACK, "slot"))
    parts.append(fbox(-2.25, -1.6, -1.5, 1.6, 0.0, 1.02, TIN, "term"))
    parts.append(fbox(1.5, -1.6, 2.25, 1.6, 0.0, 1.02, TIN, "term"))
    return parts


def wago_2060_3p():
    """WAGO 2060-453/998-404: 3 poles 4.0 mm pitch, 4.5 mm tall, wire entry at -x, release buttons on top."""
    from build123d import Axis
    body = fbox(-7.62, -5.95, 6.38, 5.95, 0.0, 4.5, (0.86, 0.87, 0.89))
    for py in (-4.0, 0.0, 4.0):
        hole = Location((-7.62, -py, 2.1)) * Cylinder(0.9, 8.0).rotate(Axis.Y, 90)
        body = body - hole
    body.color = Color(0.86, 0.87, 0.89)
    body.label = "housing"
    parts = [body]
    for py in (-4.0, 0.0, 4.0):
        parts.append(fbox(0.3, py - 1.0, 5.8, py + 1.0, 4.3, 4.65, ORANGE, "pusher"))
        parts.append(fbox(-7.2, py - 0.9, -2.0, py + 0.9, 0.0, 0.1, TIN, "contact"))
        parts.append(fcyl(-7.0, py, 1.3, 2.9, 1.5, (0.15, 0.15, 0.17), "entry"))  # dark hole bottom
    return parts


def lcd_114():
    """1.14in 135x240 IPS module on its 13-pin FPC, display side away from the board (+z)."""
    gx0, gx1, gy = -46.2, -15.2, 8.8
    parts = [
        fbox(gx0, -gy, gx1, gy, 0.2, 1.15, WHITE, "backlight"),
        fbox(gx0, -gy, gx1, gy, 1.15, 1.8, GLASS, "glass"),
        fbox(-44.6, -7.43, -19.69, 7.43, 1.8, 1.81, SCREEN, "active"),
        fbox(gx1 - 0.6, -6.2, gx1 + 0.1, 6.2, 0.0, 1.15, AMBER, "fpc_bend"),
        fbox(gx1, -6.2, 0.65, 6.2, 0.0, 0.12, AMBER, "fpc"),
    ]
    for i in range(13):
        y = -4.2 + 0.7 * i
        parts.append(fbox(-1.4, y - 0.15, 0.6, y + 0.15, 0.12, 0.16, (0.85, 0.7, 0.3), "finger"))
    return parts


# ---- footprint -> model ------------------------------------------------------------------------

def model_for(name: str, pads: list):
    if "_0402_" in name or "_0603_" in name or "_0805_" in name:
        size = next(s for s in CHIP if f"_{s}_" in name)
        kind = "R" if name.startswith("R_") else "L" if name.startswith("L_") else "F" if name.startswith("Fuse") else "C"
        return chip(kind, size)
    table = {
        "SOT-23": lambda: gullwing(pads, 0.65, 1.45, 1.0),
        "SOT-23-5": lambda: gullwing(pads, 0.8, 1.45, 1.1),
        "SOT-23-6": lambda: gullwing(pads, 0.8, 1.45, 1.1),
        "D_SOD-323": lambda: gullwing(pads, 0.85, 0.65, 0.9, pin1=False, band_x=-0.55),
        "MSOP-8_3x3mm_P0.65mm": lambda: gullwing(pads, 1.5, 1.5, 0.95),
        "TSSOP-8_3x3mm_P0.65mm": lambda: gullwing(pads, 1.5, 1.5, 1.0),
        "LQFP-48_7x7mm_P0.5mm": lambda: gullwing(pads, 3.5, 3.5, 1.4),
        "Crystal_SMD_2520-4Pin_2.5x2.0mm": crystal_2520,
        "Crystal_SMD_3225-4Pin_3.2x2.5mm": crystal_3225,
        "JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal": lambda: jst_sh6_horizontal(pads),
        "SW_Push_Side_TS24CA": lambda: ts24ca(pads),
        "LED_Side_XL3806_3.8x1.0mm": lambda: led_xl3806(pads),
        "C_Trimmer_Knowles_JZ100_4.5x3.2mm": jz100,
        "Nose_WAGO2060_KF381_ProbePads": wago_2060_3p,
        "LCD_FPC_13P_P0.70mm_Solder": lcd_114,
    }
    f = table.get(name)
    return f() if f else None


def main() -> None:
    geom = json.loads(GEOM.read_text(encoding="utf8"))
    made, skipped = [], []
    for fpid, g in geom.items():
        name = fpid.split(":", 1)[1]
        parts = model_for(name, g["pads"])
        if parts is None:
            skipped.append(name)
            continue
        save(name, parts)
        made.append(name)
    print(f"wrote {len(made)} models to {OUT}")
    print("no model (vendor model or bare pads):", ", ".join(skipped))


if __name__ == "__main__":
    main()
