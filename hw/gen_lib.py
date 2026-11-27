"""Write the project-local KiCad library hw/lib (symbols ppa.kicad_sym, footprints ppa.pretty).

Run with KiCad's Python:
    kicad-python hw/gen_lib.py

Datasheet sources:
  AD9288              Analog Devices AD9288BSTZ-80 (C514284), LQFP-48 (Table 3 pinout)
  RS8752              Jiangsu Runic RS8752XM (C255421), MSOP-8
  74LVC2G66           Nexperia 74LVC2G66DP,125 (C282354), TSSOP-8 (SOT505-2)
  RT9013-33GB         Richtek RT9013 (DS9013-10), SOT-23-5
  BAV199              Nexperia BAV199 (2020-09-01), SOT-23
  LCD_FPC_13P_Solder  1.14" 135x240 ST7789 / 0.96" 80x160 ST7735S 13P 0.70 mm solder FPC
  TS24CA              SHOU HAN TS24CA (C393942) 4.7x3.5x1.9 mm side-actuated SMD tactile switch
  Nose_WAGO2060_KF381 WAGO 2060-453/998-404 3P 4.0 mm SMD + DNP KF250NH-3.81-3P THT + 14x2.8 mm F/B via-stitched probe pads
  JZ100               Knowles Voltronics JZ100 trimmer capacitor (5.5 mm span, 2.0 mm gap, 4.0 mm pad height)
  LED_Side_XL3806     XINGLIGHT XL-3806UWC (C2843778) 3.8x1.0x0.6 mm side-emitting white LED (1=K, 2=A)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
PRETTY = LIB / "ppa.pretty"
SYM = LIB / "ppa.kicad_sym"


# ----------------------------------------------------------------------------------------------
# Symbols (S-expressions)
# ----------------------------------------------------------------------------------------------
def _prop(name, value, x, y, hide=False, justify=""):
    h = " (hide yes)" if hide else ""
    j = f" (justify {justify})" if justify else ""
    return (
        f'\t\t(property "{name}" "{value}" (at {x} {y} 0)\n'
        f'\t\t\t(effects (font (size 1.27 1.27)){j}{h})\n\t\t)\n'
    )


def _pin(num, name, kind, x, y, angle, length=2.54):
    return (
        f'\t\t\t(pin {kind} line (at {x} {y} {angle}) (length {length})\n'
        f'\t\t\t\t(name "{name}" (effects (font (size 1.27 1.27))))\n'
        f'\t\t\t\t(number "{num}" (effects (font (size 1.27 1.27))))\n\t\t\t)\n'
    )


def _symbol(name, ref, value, desc, footprint, body, pins, graphics="", fields_right=False):
    x0, y0, x1, y1 = body
    s = f'\t(symbol "{name}"\n'
    s += "\t\t(exclude_from_sim no) (in_bom yes) (on_board yes)\n"
    if fields_right:
        s += _prop("Reference", ref, x1 + 1.27, 1.27, justify="left")
        s += _prop("Value", value, x1 + 1.27, -1.27, justify="left")
    else:
        s += _prop("Reference", ref, x0, y1 + 1.27, justify="left")
        s += _prop("Value", value, x0, y0 - 1.27, justify="left")
    s += _prop("Footprint", footprint, 0, 0, hide=True)
    s += _prop("Datasheet", "", 0, 0, hide=True)
    s += _prop("Description", desc, 0, 0, hide=True)
    s += f'\t\t(symbol "{name}_0_1"\n'
    s += (
        f"\t\t\t(rectangle (start {x0} {y0}) (end {x1} {y1})\n"
        "\t\t\t\t(stroke (width 0.254) (type default)) (fill (type background))\n\t\t\t)\n"
    )
    s += graphics
    s += "\t\t)\n"
    s += f'\t\t(symbol "{name}_1_1"\n'
    for p in pins:
        s += _pin(*p)
    s += "\t\t)\n\t\t(embedded_fonts no)\n\t)\n"
    return s


def _poly(points, width=0.254):
    pts = " ".join(f"(xy {x} {y})" for x, y in points)
    return (
        f"\t\t\t(polyline (pts {pts})\n"
        f"\t\t\t\t(stroke (width {width}) (type default)) (fill (type none))\n\t\t\t)\n"
    )


def symbols() -> str:
    out = ['(kicad_symbol_lib\n\t(version 20251024)\n\t(generator "ppa_gen_lib")\n\t(generator_version "10.0")\n']

    # 1. AD9288 (ADI AD9288BSTZ-80, LQFP-48 Table 3)
    L, R = -15.24, 15.24
    left = [
        ("2", "AINA", "input"),
        ("3", "~{AINA}", "input"),
        ("11", "AINB", "input"),
        ("10", "~{AINB}", "input"),
        ("5", "REFINA", "input"),
        ("6", "REFOUT", "passive"),
        ("7", "REFINB", "input"),
        ("4", "DFS", "input"),
        ("8", "S1", "input"),
        ("9", "S2", "input"),
        ("47", "ENCA", "input"),
        ("14", "ENCB", "input"),
        ("25", "NC", "no_connect"),
        ("26", "NC", "no_connect"),
        ("35", "NC", "no_connect"),
        ("36", "NC", "no_connect"),
    ]
    right = [
        ("37", "D0A", "output"), ("38", "D1A", "output"), ("39", "D2A", "output"), ("40", "D3A", "output"),
        ("41", "D4A", "output"), ("42", "D5A", "output"), ("43", "D6A", "output"), ("44", "D7A", "output"),
        ("24", "D0B", "output"), ("23", "D1B", "output"), ("22", "D2B", "output"), ("21", "D3B", "output"),
        ("20", "D4B", "output"), ("19", "D5B", "output"), ("18", "D6B", "output"), ("17", "D7B", "output"),
    ]
    vd_pins = [("13", "VD"), ("30", "VD"), ("31", "VD"), ("48", "VD")]
    vdd_pins = [("15", "VDD"), ("28", "VDD"), ("33", "VDD"), ("46", "VDD")]
    gnd_pins = [
        ("1", "GND"), ("12", "GND"), ("16", "GND"), ("27", "GND"),
        ("29", "GND"), ("32", "GND"), ("34", "GND"), ("45", "GND"),
    ]
    pins = []
    for i, (n, nm) in enumerate(vd_pins):
        pins.append((n, nm, "power_in", -10.16 + 2.54 * i, 25.4, 270))
    for i, (n, nm) in enumerate(vdd_pins):
        pins.append((n, nm, "power_in", 2.54 + 2.54 * i, 25.4, 270))
    for i, (n, nm) in enumerate(gnd_pins):
        pins.append((n, nm, "power_in", -8.89 + 2.54 * i, -25.4, 90))
    pins += [(n, nm, k, L, 19.05 - 2.54 * i, 0) for i, (n, nm, k) in enumerate(left)]
    pins += [(n, nm, k, R, 19.05 - 2.54 * i, 180) for i, (n, nm, k) in enumerate(right)]
    out.append(_symbol(
        "AD9288", "U", "AD9288BSTZ-80",
        "Analog Devices AD9288 dual 8-bit 80 MSPS parallel ADC, LQFP-48",
        "Package_QFP:LQFP-48_7x7mm_P0.5mm",
        (-12.7, -22.86, 12.7, 22.86), pins, fields_right=True,
    ))

    # 1b. RS8752 (Jiangsu Runic RS8752XM, MSOP-8)
    L, R = -12.7, 12.7
    pins = [
        ("8", "V+", "power_in", 0, 11.43, 270),
        ("4", "V-", "power_in", 0, -11.43, 90),
        ("3", "+INA", "input", L, 5.08, 0),
        ("2", "-INA", "input", L, 2.54, 0),
        ("5", "+INB", "input", L, -2.54, 0),
        ("6", "-INB", "input", L, -5.08, 0),
        ("1", "OUTA", "output", R, 3.81, 180),
        ("7", "OUTB", "output", R, -3.81, 180),
    ]
    out.append(_symbol(
        "RS8752", "U", "RS8752XM",
        "Runic RS8752XM dual 250 MHz 1 pA CMOS RR-out op-amp, MSOP-8",
        "Package_SO:MSOP-8_3x3mm_P0.65mm",
        (-10.16, -8.89, 10.16, 8.89), pins, fields_right=True,
    ))

    # 2. 74LVC2G66 (Nexperia 74LVC2G66DP,125 SOT505-2 / TSSOP-8; 1E=pin7, 2E=pin3)
    L, R = -12.7, 12.7
    pins = [
        ("8", "VCC", "power_in", 0, 11.43, 270),
        ("4", "GND", "power_in", 0, -11.43, 90),
        ("1", "1Y", "passive", L, 5.08, 0),
        ("7", "1E", "input", L, 2.54, 0),
        ("5", "2Y", "passive", L, -2.54, 0),
        ("3", "2E", "input", L, -5.08, 0),
        ("2", "1Z", "passive", R, 3.81, 180),
        ("6", "2Z", "passive", R, -3.81, 180),
    ]
    out.append(_symbol(
        "74LVC2G66", "U", "74LVC2G66DP",
        "Nexperia 74LVC2G66DP dual independent 440 MHz 6 ohm SPST analog switch, TSSOP-8",
        "Package_SO:TSSOP-8_3x3mm_P0.65mm",
        (-10.16, -8.89, 10.16, 8.89), pins, fields_right=True,
    ))

    # 4. RT9013-33GB (Richtek DS9013-10, SOT-23-5)
    pins = [
        ("1", "VIN", "power_in", -10.16, 2.54, 0),
        ("3", "EN", "input", -10.16, -2.54, 0),
        ("2", "GND", "power_in", 0, -8.89, 90),
        ("5", "VOUT", "power_out", 10.16, 2.54, 180),
        ("4", "NC", "no_connect", 10.16, -2.54, 180),
    ]
    out.append(_symbol(
        "RT9013-33GB", "U", "RT9013-33GB",
        "Richtek RT9013-33GB 500 mA low-noise 3.3 V LDO, SOT-23-5",
        "Package_TO_SOT_SMD:SOT-23-5",
        (-7.62, -6.35, 7.62, 6.35), pins, fields_right=True,
    ))

    # 5. BAV199 (Nexperia BAV199 2020-09-01, SOT-23)
    # 1=A1, 2=K2, 3=K1_A2
    g = (
        _poly([(-2.54, 1.27), (0, 2.54), (-2.54, 3.81), (-2.54, 1.27)])
        + _poly([(0, 1.27), (0, 3.81)])
        + _poly([(0, -3.81), (2.54, -2.54), (0, -1.27), (0, -3.81)])
        + _poly([(2.54, -3.81), (2.54, -1.27)])
        + _poly([(0, 2.54), (3.81, 2.54), (3.81, 0), (-3.81, 0), (-3.81, -2.54), (0, -2.54)], 0.1524)
    )
    pins = [
        ("1", "A1", "passive", -7.62, 2.54, 0),
        ("2", "K2", "passive", -7.62, -2.54, 0),
        ("3", "K1_A2", "passive", 7.62, 0, 180),
    ]
    out.append(_symbol(
        "BAV199", "D", "BAV199",
        "Nexperia BAV199 ultra-low-leakage (<3 pA typ) dual series diode, SOT-23",
        "Package_TO_SOT_SMD:SOT-23",
        (-5.08, -5.08, 5.08, 5.08), pins, g,
    ))

    # 6. SW_Side_TS24CA (SHOU HAN TS24CA / C393942 4-pad side-actuated SMD tactile switch)
    g_sw = _poly([(-2.54, 1.27), (2.54, 2.54)]) + _poly([(-2.54, -2.54), (2.54, -2.54)])
    pins = [
        ("1", "A", "passive", -7.62, 1.27, 0),
        ("2", "B", "passive", 7.62, 1.27, 180),
        ("3", "SH", "passive", 7.62, -2.54, 180),
        ("4", "SH", "passive", -7.62, -2.54, 0),
    ]
    out.append(_symbol(
        "SW_Side_TS24CA", "SW", "TS24CA",
        "SHOU HAN TS24CA right-angle SMD tactile push button (1=A, 2=B, 3/4=shield)",
        "ppa:SW_Push_Side_TS24CA",
        (-5.08, -3.81, 5.08, 3.81), pins, g_sw,
    ))

    out.append(")\n")
    return "".join(out)


# ----------------------------------------------------------------------------------------------
# Footprints (built via KiCad pcbnew)
# ----------------------------------------------------------------------------------------------
MM = pcbnew.FromMM


def _v(x, y):
    return pcbnew.VECTOR2I(MM(x), MM(y))


def _new_fp(name, descr, attr=pcbnew.FP_SMD):
    fp = pcbnew.FOOTPRINT(None)
    fp.SetFPID(pcbnew.LIB_ID("ppa", name))
    fp.SetLibDescription(descr)
    fp.SetAttributes(attr)
    fp.Reference().SetText("REF**")
    fp.Value().SetText(name)
    fp.Value().SetLayer(pcbnew.F_Fab)
    return fp


def _smd(fp, num, x, y, w, h, ratio=0.15):
    p = pcbnew.PAD(fp)
    p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    p.SetLayerSet(p.SMDMask())
    p.SetNumber(str(num))
    p.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_ROUNDRECT)
    p.SetRoundRectRadiusRatio(pcbnew.PADSTACK.ALL_LAYERS, ratio)
    p.SetSize(pcbnew.PADSTACK.ALL_LAYERS, _v(w, h))
    p.SetPosition(_v(x, y))
    fp.Add(p)
    return p


def _pth(fp, num, x, y, size, drill, shape="circle"):
    p = pcbnew.PAD(fp)
    p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    p.SetLayerSet(p.PTHMask())
    p.SetNumber(str(num))
    shapes = {"circle": pcbnew.PAD_SHAPE_CIRCLE, "rect": pcbnew.PAD_SHAPE_RECT}
    p.SetShape(pcbnew.PADSTACK.ALL_LAYERS, shapes[shape])
    p.SetSize(pcbnew.PADSTACK.ALL_LAYERS, _v(size, size))
    p.SetDrillSize(_v(drill, drill))
    p.SetPosition(_v(x, y))
    fp.Add(p)
    return p


def _rect(fp, layer, x0, y0, x1, y1, w):
    s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_RECT)
    s.SetLayer(layer)
    s.SetStart(_v(x0, y0))
    s.SetEnd(_v(x1, y1))
    s.SetWidth(MM(w))
    fp.Add(s)


def _line(fp, layer, x0, y0, x1, y1, w):
    s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(layer)
    s.SetStart(_v(x0, y0))
    s.SetEnd(_v(x1, y1))
    s.SetWidth(MM(w))
    fp.Add(s)


def _circle(fp, layer, r, w):
    s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)
    s.SetLayer(layer)
    s.SetCenter(_v(0, 0))
    s.SetEnd(_v(r, 0))
    s.SetWidth(MM(w))
    fp.Add(s)


def fp_lcd_fpc_13p():
    """13-pin 0.70 mm pitch solder-on FPC footprint (1.14 in ST7789 & 0.96 in ST7735S compatible, DNP on B.Cu)."""
    fp = _new_fp(
        "LCD_FPC_13P_P0.70mm_Solder",
        "13-pin 0.70 mm pitch bare solder-on FPC landing (1.14 in & 0.96 in IPS LCDs)",
        attr=pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES | pcbnew.FP_DNP,
    )
    cu_mask_only = pcbnew.LSET()
    cu_mask_only.AddLayer(pcbnew.F_Cu)
    cu_mask_only.AddLayer(pcbnew.F_Mask)
    for i in range(13):
        # On F.Cu before Flip(pos, False), Y goes -4.20 -> +4.20 so after Flip to B.Cu: Pin 1 is +4.20, Pin 13 is -4.20
        y = -4.20 + i * 0.70
        p = _smd(fp, i + 1, 0.0, y, 3.00, 0.40, ratio=0.08)
        p.SetLayerSet(cu_mask_only)
    # 1.14" glass outline (-46.20..-15.20, y=+-8.80) & 0.96" glass outline (-43.15..-15.20, y=+-6.75) on Fab
    _rect(fp, pcbnew.F_Fab, -46.20, -8.80, -15.20, 8.80, 0.12)
    _rect(fp, pcbnew.F_Fab, -43.15, -6.75, -15.20, 6.75, 0.08)
    _rect(fp, pcbnew.F_Fab, -15.20, -6.20, 0.65, 6.20, 0.10)  # FPC tab (tip at x = +0.65 -> 88.85 mm)
    for sy in (-5.20, 5.20):  # 10.40 mm alignment hole targets on Fab
        s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)
        s.SetLayer(pcbnew.F_Fab)
        s.SetCenter(_v(-3.35, sy))
        s.SetEnd(_v(-3.05, sy))
        s.SetWidth(MM(0.10))
        fp.Add(s)
    # Placement guide on silk (lands on B.SilkS): 1.14" glass outline (kept 0.35 mm inside the board edge),
    # FPC tail up to the pads, corner marks of the visible area, ticks where the 0.96" glass ends
    _rect(fp, pcbnew.F_SilkS, -46.00, -8.45, -15.40, 8.45, 0.15)
    for sy in (-6.20, 6.20):
        _line(fp, pcbnew.F_SilkS, -15.40, sy, -2.10, sy, 0.15)
    ax0, ax1, ay = -44.60, -19.69, 7.43
    for cx, dx in ((ax0, 1.5), (ax1, -1.5)):
        for cy, dy in ((-ay, 1.5), (ay, -1.5)):
            _line(fp, pcbnew.F_SilkS, cx, cy, cx + dx, cy, 0.12)
            _line(fp, pcbnew.F_SilkS, cx, cy, cx, cy + dy, 0.12)
    for sy in (-1, 1):
        _line(fp, pcbnew.F_SilkS, -43.15, sy * 6.75, -43.15, sy * 5.55, 0.12)
    for txt, x, y, size in (("LCD 1.14in glass, display out", -31.0, 0.0, 0.8),
                            ("0.96in", -40.0, 6.05, 0.8)):
        t = pcbnew.PCB_TEXT(fp)
        t.SetText(txt)
        t.SetLayer(pcbnew.F_SilkS)
        t.SetPosition(_v(x, y))
        t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
        t.SetTextThickness(MM(size * 0.15))
        fp.Add(t)
    # Minimal SilkS pin-1 dot clear of pads & adjacent copper
    s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)
    s.SetLayer(pcbnew.F_SilkS)
    s.SetCenter(_v(1.90, -4.20))
    s.SetEnd(_v(2.10, -4.20))
    s.SetWidth(MM(0.18))
    fp.Add(s)
    _rect(fp, pcbnew.F_CrtYd, -1.75, -4.65, 1.75, 4.65, 0.05)
    fp.Reference().SetPosition(_v(2.8, 0.0))
    fp.Value().SetPosition(_v(-30.0, 0.0))
    return fp


def fp_sw_ts24ca():
    """SHOU HAN TS24CA (C393942) 4.7x3.5x1.9 mm pure-SMD side-actuated tactile switch (plunger faces -Y at rot=0)."""
    fp = _new_fp(
        "SW_Push_Side_TS24CA",
        "SHOU HAN TS24CA right-angle SMD tactile push button (LCSC C393942)",
    )
    _smd(fp, 1, -1.700, 0.975, 0.600, 1.200, ratio=0.1)
    _smd(fp, 2, 1.700, 0.975, 0.600, 1.200, ratio=0.1)
    _smd(fp, 3, 2.125, -0.975, 1.050, 1.300, ratio=0.1)
    _smd(fp, 4, -2.125, -0.975, 1.050, 1.300, ratio=0.1)
    _rect(fp, pcbnew.F_Fab, -2.275, -1.225, 2.275, 1.075, 0.10)
    _rect(fp, pcbnew.F_Fab, -1.000, -2.425, 1.000, -1.225, 0.10)  # plunger
    _line(fp, pcbnew.F_SilkS, -1.10, 1.25, 1.10, 1.25, 0.12)
    _line(fp, pcbnew.F_SilkS, -1.10, 0.70, -1.10, 1.25, 0.12)  # pin 1 tick
    _rect(fp, pcbnew.F_CrtYd, -2.90, -2.55, 2.90, 1.80, 0.05)
    fp.Reference().SetPosition(_v(0, 2.5))
    fp.Value().SetPosition(_v(0, -3.1))
    return fp


def fp_nose_wago2060_kf381_probe():
    """3-in-1 nose combo: WAGO 2060-453 SMD (4.0 mm) + DNP KF250NH-3.81-3P THT + 14x2.8 mm F/B via-stitched probe pads."""
    fp = _new_fp(
        "Nose_WAGO2060_KF381_ProbePads",
        "WAGO 2060-453/998-404 3P 4.0 mm SMD spring terminal + DNP KF250NH-3.81-3P THT + 14x2.8 mm F/B via-stitched probe pads",
        attr=pcbnew.FP_SMD,
    )
    f_cu_mask = pcbnew.LSET()
    f_cu_mask.AddLayer(pcbnew.F_Cu)
    f_cu_mask.AddLayer(pcbnew.F_Mask)
    b_cu_mask = pcbnew.LSET()
    b_cu_mask.AddLayer(pcbnew.B_Cu)
    b_cu_mask.AddLayer(pcbnew.B_Mask)

    poles = ((1, -4.00, -3.81), (2, 0.00, 0.00), (3, 4.00, 3.81))
    via_xs = (-3.20, -1.30, 2.30, 4.20, 5.90)

    for num, y_wago, y_kf in poles:
        # 1. Heavy-duty flat solder pads: 14.0 x 2.80 mm on F.Cu, shortened 10.8 x 2.80 mm on B.Cu
        # (leaves x = 0..4.2 mm at nose tip clear on B.Cu for always-on front worklight LEDs)
        pt = _smd(fp, num, -0.50, y_wago, 14.00, 2.80, ratio=0.06)
        pt.SetLayerSet(f_cu_mask)
        pb = _smd(fp, num, 1.10, y_wago, 10.80, 2.80, ratio=0.06)
        pb.SetLayerSet(b_cu_mask)

        # 2. WAGO 2060-453/998-404 SMD solder paste zones (C2765056: 6.0x2.0 front, 3.5x2.0 rear)
        _smd(fp, num, -4.625, y_wago, 6.00, 2.00, ratio=0.10)
        _smd(fp, num, 4.625, y_wago, 3.50, 2.00, ratio=0.10)

        # 3. DNP KF250NH-3.81-3P (C976577) THT holes: front row in gap between WAGO paste pads, rear row behind
        _pth(fp, num, 0.50, y_kf, 2.30, 1.50, "circle")
        _pth(fp, num, 7.50, y_kf, 2.30, 1.50, "circle")

        # 4. 10 through-board stitching vias per pole (30 total) mechanically locking F.Cu and B.Cu pads
        for vx in via_xs:
            for vy in (y_wago - 1.00, y_wago + 1.00):
                _pth(fp, num, vx, vy, 0.55, 0.30, "circle")

    # WAGO 2060-453 body & top push-buttons on F.Fab; probe solder zones on B.Fab
    _rect(fp, pcbnew.F_Fab, -7.62, -5.95, 6.38, 5.95, 0.12)
    for _, y_wago, _ in poles:
        _rect(fp, pcbnew.F_Fab, 0.00, y_wago - 1.10, 6.10, y_wago + 1.10, 0.10)
        _rect(fp, pcbnew.B_Fab, -4.30, y_wago - 1.40, 6.50, y_wago + 1.40, 0.10)
    # Optional KF250NH-3.81-3P body outline on F.Fab (x = -2.0 .. +10.0, y = -6.0 .. +6.0)
    _rect(fp, pcbnew.F_Fab, -2.00, -6.00, 10.00, 6.00, 0.08)

    # Silkscreen outline outside exposed copper pads (y = +-5.95 clear of y = +-5.40 pads)
    _line(fp, pcbnew.F_SilkS, -7.62, -6.05, 6.38, -6.05, 0.12)
    _line(fp, pcbnew.F_SilkS, -7.62, 6.05, 6.38, 6.05, 0.12)
    _rect(fp, pcbnew.F_CrtYd, -7.80, -6.25, 8.85, 6.25, 0.05)
    fp.Reference().SetPosition(_v(0, -6.6))
    fp.Value().SetPosition(_v(0, 6.6))
    return fp


def fp_jz100():
    """Knowles Voltronics JZ100 trimmer capacitor (5.5 mm outer span, 2.0 mm gap, 4.0 mm pad height)."""
    fp = _new_fp(
        "C_Trimmer_Knowles_JZ100_4.5x3.2mm",
        "Knowles Voltronics JZ100 2-10 pF SMD trimmer capacitor, 4.5x3.2 mm",
    )
    _smd(fp, 1, -1.875, 0, 1.75, 4.0, ratio=0.08)
    _smd(fp, 2, 1.875, 0, 1.75, 4.0, ratio=0.08)
    _rect(fp, pcbnew.F_Fab, -2.25, -1.60, 2.25, 1.60, 0.1)
    for y in (-2.25, 2.25):
        _line(fp, pcbnew.F_SilkS, -0.75, y, 0.75, y, 0.12)
    _rect(fp, pcbnew.F_CrtYd, -3.00, -2.35, 3.00, 2.35, 0.05)
    fp.Reference().SetPosition(_v(0, -3.2))
    fp.Value().SetPosition(_v(0, 3.2))
    return fp


def fp_led_xl3806():
    """XINGLIGHT XL-3806UWC (C2843778) 3.8x1.0x0.6 mm side-view white LED (emitting edge -Y at rot=0; 1=K, 2=A)."""
    fp = _new_fp(
        "LED_Side_XL3806_3.8x1.0mm",
        "XINGLIGHT XL-3806UWC 3.8x1.0x0.6 mm side-emitting white LED (1=K, 2=A, emits -Y at rot=0)",
    )
    _smd(fp, 1, -1.55, 0.0, 0.90, 1.00, ratio=0.10)
    _smd(fp, 2, 1.55, 0.0, 0.90, 1.00, ratio=0.10)
    _rect(fp, pcbnew.F_Fab, -1.90, -0.50, 1.90, 0.50, 0.10)
    _line(fp, pcbnew.F_Fab, -1.90, -0.50, 1.90, -0.50, 0.18)  # emitting lens face (-Y)
    _line(fp, pcbnew.F_Fab, -1.55, -0.45, -1.55, 0.45, 0.15)  # cathode marker
    _line(fp, pcbnew.F_SilkS, -2.15, -0.45, -2.15, 0.45, 0.15)  # cathode tick
    _line(fp, pcbnew.F_SilkS, -1.00, -0.68, 1.00, -0.68, 0.14)  # emitting edge indicator
    _rect(fp, pcbnew.F_CrtYd, -2.10, -0.70, 2.10, 0.70, 0.05)
    fp.Reference().SetPosition(_v(0, 1.35))
    fp.Value().SetPosition(_v(0, -1.35))
    return fp


def main():
    LIB.mkdir(exist_ok=True)
    SYM.write_text(symbols(), encoding="utf8", newline="\n")
    if PRETTY.exists():
        shutil.rmtree(PRETTY)
    PRETTY.mkdir()
    io = pcbnew.PCB_IO_KICAD_SEXPR()
    for f in (fp_lcd_fpc_13p(), fp_sw_ts24ca(), fp_nose_wago2060_kf381_probe(), fp_jz100(), fp_led_xl3806()):
        io.FootprintSave(str(PRETTY), f)
    print("wrote", SYM, "and", sorted(os.listdir(PRETTY)))


if __name__ == "__main__":
    main()
