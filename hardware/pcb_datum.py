"""Single mechanical coordinate source of truth for KiCad and build123d.
Origin (0, 0) = Front tip center of PCB. +X points toward USB-C tail.

S-curve tapered pen outline (138.0 x 17.60 mm max, matching 1.14" LCD glass width):
- Nose section (x = 0 .. 18 mm): 14.00 mm wide (Y in -7.00 .. +7.00 mm), corner radius 2.0 mm.
- C1 two-arc S-curve taper (x = 18 .. 28 mm): widens smoothly from 14.00 mm to 17.60 mm.
- Main body & tail (x = 28 .. 138 mm): 17.60 mm wide (Y in -8.80 .. +8.80 mm), tail radius 2.0 mm.

Note on Y convention:
- KiCad coordinates use Y-down (positive Y toward bottom of screen / SW1..SW4 side edge).
- Right-handed CAD coordinates (`to_features`) use Y-up (negative Y = KiCad +Y).
"""
from __future__ import annotations

import math

PCB = {
    "length": 138.0,
    "nose_width": 14.0,                  # Tapered 14.0 mm front nose (2x XL-3806UWC LEDs on B.Cu tip)
    "taper_x": (18.0, 28.0),             # C1 S-curve transition from 14.0 mm nose to 17.6 mm body
    "width": 17.60,                      # 17.60 mm across main body (exact 1.14" LCD glass width)
    "thickness": 1.6,
    "nose_radius": 2.0,                  # 2.0 mm corner radius clears 11.9 mm WAGO 2060-453 body
    "tail_radius": 2.0,
    "nose_term_xy": (8.5, 0.0),          # 3-in-1 nose terminal (WAGO 2060-453 + DNP 3.81 THT + F/B probe pads)
    "led_top_xy": (2.2, -2.8),           # XL-3806UWC #1 front worklight (B.Cu nose tip, fires -X)
    "led_bot_xy": (2.2, 2.8),            # XL-3806UWC #2 front worklight (B.Cu nose tip, fires -X)
    "afe_cutout_x": (1.0, 26.2),         # Layer-2/3 pour keepout under nose & 1 MOhm HV dividers
    "afe_cutout_y": (-6.0, 6.0),
    "mount_hole_front_xy": (29.0, 0.0),  # Unplated 2.2 mm M2 NPTH mounting hole #1 (front waist)
    "btn_ask_ai_xy": (34.0, 6.8),        # Side-actuated TS24CA #1 (KiCad +Y edge, plunger out +Y)
    "btn_mode_xy": (42.5, 6.8),          # Side-actuated TS24CA #2 (Mode / BOOTSEL)
    "btn_up_xy": (51.0, 6.8),            # Side-actuated TS24CA #3 (Up / +)
    "btn_down_xy": (59.5, 6.8),          # Side-actuated TS24CA #4 (Down / -)
    "lcd_side": "B",                     # 1.14"/0.96" 13P solder-on LCD mounted flat on B.Cu
    "lcd_glass_x": (32.0, 63.0),         # 1.14" 135x240 glass 17.60 x 31.00 x 1.60 mm (0.96" sits 35.05..63.0)
    "lcd_glass_width": 17.60,            # exact match to PCB main body width
    "lcd_glass_thickness": 1.60,
    "lcd_active_x": (33.6, 58.51),       # 1.14" Active Area: 24.91 x 14.86 mm (1.60 mm from far edge)
    "lcd_active_width": 14.86,
    "lcd_fpc_xy": (78.2, 0.0),           # 13P 0.70 mm-pitch solder pads on B.Cu (FPC tip at X = 78.85)
    "ad9288_xy": (85.0, 0.0),           # AD9288BSTZ-80 LQFP-48 dual 80 MSPS ADC (centered)
    "rp2354b_xy": (104.5, 0.0),          # RP2354B QFN-80 (10x10mm, west of expansion header)
    "side_jst_xy": (119.5, 5.4),         # 6P JST-SH right-angle side header (USB-C side of MCU, CAD Y-up -> KiCad Y=-5.4)
    "mount_hole_rear_xy": (127.4, 0.0),  # Unplated 2.2 mm M2 NPTH mounting hole #2 (against J1 USB-C)
    "usbc_xy": (135.3, 0.0),             # 16P USB-C receptacle (1 mm tail overhang past X=138)
}


def scurve_params() -> tuple[float, float, float, float, float, float]:
    """Return (tx0, tx1, x_mid, y_mid, r_s, hb) for the C1 two-arc S-curve taper."""
    hn = float(PCB["nose_width"]) / 2.0
    hb = float(PCB["width"]) / 2.0
    tx0, tx1 = (float(v) for v in PCB["taper_x"])
    dx_h = 0.5 * (tx1 - tx0)
    dy_h = 0.5 * (hb - hn)
    r_s = ((dx_h * dx_h + dy_h * dy_h) / (2.0 * dy_h)) if abs(dy_h) > 1e-9 else float("inf")
    return tx0, tx1, tx0 + dx_h, hn + dy_h, r_s, hb


def half_width_at(x: float, margin: float = 0.0) -> float:
    """PCB half-width (mm) at coordinate x, optionally inset by perpendicular `margin`."""
    hn = float(PCB["nose_width"]) / 2.0
    hb = float(PCB["width"]) / 2.0
    if abs(hb - hn) < 1e-9:
        return hb - margin
    tx0, tx1, x_mid, y_mid, r_s, hb = scurve_params()
    theta = math.atan2(x_mid - tx0, r_s - (y_mid - hn))
    if margin == 0.0:
        if x <= tx0:
            return hn
        if x >= tx1:
            return hb
        if x <= x_mid:
            return hn + r_s - math.sqrt(max(0.0, r_s * r_s - (x - tx0) ** 2))
        return hb - r_s + math.sqrt(max(0.0, r_s * r_s - (tx1 - x) ** 2))

    x_inf = x_mid + margin * math.sin(theta)
    if x <= tx0:
        return hn - margin
    if x >= tx1:
        return hb - margin
    if x <= x_inf:
        r_in = r_s + margin
        return hn + r_s - math.sqrt(max(0.0, r_in * r_in - (x - tx0) ** 2))
    r_out = max(0.2, r_s - margin)
    return hb - r_s + math.sqrt(max(0.0, r_out * r_out - (tx1 - x) ** 2))


def point_in_pcb(x: float, y: float, margin: float = 0.0) -> bool:
    """True when (x, y) lies at least `margin` mm inside the smooth S-curve rounded-corner PCB outline."""
    length = float(PCB["length"])
    rn = float(PCB["nose_radius"])
    rt = float(PCB["tail_radius"])
    hn = float(PCB["nose_width"]) / 2.0
    hb = float(PCB["width"]) / 2.0

    if x < margin or x > length - margin:
        return False
    if abs(y) > half_width_at(x, margin=margin):
        return False

    if x < rn and abs(y) > hn - rn:
        if math.hypot(rn - x, abs(y) - (hn - rn)) > rn - margin:
            return False
    if x > length - rt and abs(y) > hb - rt:
        if math.hypot(x - (length - rt), abs(y) - (hb - rt)) > rt - margin:
            return False
    return True
