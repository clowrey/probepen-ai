"""Write the project-local KiCad library hw/lib (symbols ppa.kicad_sym, footprints ppa.pretty).

Run with KiCad's Python:
    kicad-python hw/gen_lib.py

Datasheet sources:
  MS9280         Hangzhou Ruimeng MS9280 V1.3 (2023-04-17), SSOP-28
  RS2227XN       Jiangsu Runic RS2227 Rev B.1, MSOP-10
  SN74LV4T125    TI SN74LV4T125 (SCLS749C), TSSOP-14 (single-unit)
  RT9013-33GB    Richtek RT9013 (DS9013-10), SOT-23-5
  BAV199         Nexperia BAV199 (2020-09-01), SOT-23
  503480-0800    Molex 503480-0800 (SD-503480-001 nominal FBH1 8P 0.5 mm layout; verify in JLC preview)
  9774030360R    Wurth Elektronik WA-SMSI 9774030360R M3x3.0 mm SMT spacer (OD 7.4 mm, hole 4.4 mm)
  JZ100          Knowles Voltronics JZ100 trimmer capacitor (5.5 mm span, 2.0 mm gap, 4.0 mm pad height)
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

    # 1. MS9280 (Ruimeng MS9280 V1.3, SSOP-28)
    L, R = -15.24, 15.24
    left = [
        ("27", "AIN", "input"),
        ("26", "VREF", "passive"),
        ("18", "REFSENSE", "input"),
        ("23", "MODE", "input"),
        ("21", "REFTS", "passive"),
        ("22", "REFTF", "passive"),
        ("24", "REFBF", "passive"),
        ("25", "REFBS", "passive"),
        ("19", "CLAMP", "input"),
        ("20", "CLAMPIN", "input"),
    ]
    right = [
        ("3", "D0", "output"), ("4", "D1", "output"), ("5", "D2", "output"),
        ("6", "D3", "output"), ("7", "D4", "output"), ("8", "D5", "output"),
        ("9", "D6", "output"), ("10", "D7", "output"), ("11", "D8", "output"),
        ("12", "D9", "output"), ("13", "OTR", "output"),
        ("15", "CLK", "input"), ("16", "THREE_STATE", "input"), ("17", "STBY", "input"),
    ]
    pins = [
        ("28", "AVDD", "power_in", -2.54, 22.86, 270),
        ("2", "DRVDD", "power_in", 2.54, 22.86, 270),
        ("1", "AVSS", "power_in", -2.54, -22.86, 90),
        ("14", "DRVSS", "power_in", 2.54, -22.86, 90),
    ]
    pins += [(n, nm, k, L, 15.24 - 2.54 * i, 0) for i, (n, nm, k) in enumerate(left)]
    pins += [(n, nm, k, R, 16.51 - 2.54 * i, 180) for i, (n, nm, k) in enumerate(right)]
    out.append(_symbol(
        "MS9280", "U", "MS9280",
        "Hangzhou Ruimeng MS9280 10-bit 35 MSPS parallel ADC, SSOP-28",
        "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm",
        (-12.7, -20.32, 12.7, 20.32), pins, fields_right=True,
    ))

    # 2. RS2227XN (Runic RS2227 Rev B.1, MSOP-10)
    L, R = -12.7, 12.7
    pins = [
        ("1", "V+", "power_in", 0, 11.43, 270),
        ("5", "GND", "power_in", 0, -11.43, 90),
        ("2", "S", "input", L, 5.08, 0),
        ("10", "~{OE}", "input", L, 2.54, 0),
        ("3", "D+", "passive", L, -2.54, 0),
        ("4", "D-", "passive", L, -5.08, 0),
        ("7", "HSD1+", "passive", R, 5.08, 180),
        ("6", "HSD1-", "passive", R, 2.54, 180),
        ("9", "HSD2+", "passive", R, -2.54, 180),
        ("8", "HSD2-", "passive", R, -5.08, 180),
    ]
    out.append(_symbol(
        "RS2227XN", "U", "RS2227XN",
        "Runic RS2227XN 550 MHz DPDT 2:1 analog switch, MSOP-10",
        "Package_SO:MSOP-10_3x3mm_P0.5mm",
        (-10.16, -8.89, 10.16, 8.89), pins, fields_right=True,
    ))

    # 3. SN74LV4T125 (TI SCLS749C, single-unit TSSOP-14)
    L, R = -12.7, 12.7
    left = [
        ("1", "1~{OE}", "input"), ("2", "1A", "input"),
        ("4", "2~{OE}", "input"), ("5", "2A", "input"),
        ("10", "3~{OE}", "input"), ("9", "3A", "input"),
        ("13", "4~{OE}", "input"), ("12", "4A", "input"),
    ]
    right = [
        ("3", "1Y", "tri_state"), ("6", "2Y", "tri_state"),
        ("8", "3Y", "tri_state"), ("11", "4Y", "tri_state"),
    ]
    pins = [
        ("14", "VCC", "power_in", 0, 15.24, 270),
        ("7", "GND", "power_in", 0, -15.24, 90),
    ]
    pins += [(n, nm, k, L, 8.89 - 2.54 * i, 0) for i, (n, nm, k) in enumerate(left)]
    pins += [(n, nm, k, R, 7.62 - 5.08 * i, 180) for i, (n, nm, k) in enumerate(right)]
    out.append(_symbol(
        "SN74LV4T125", "U", "SN74LV4T125",
        "TI SN74LV4T125 4-channel 5V-tolerant level-shifting buffer, TSSOP-14",
        "Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
        (-10.16, -12.7, 10.16, 12.7), pins, fields_right=True,
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


def fp_molex_503480():
    """Molex 503480-0800 8-pin 0.5 mm BackFlip FPC connector (SD-503480-001 nominal FBH1 layout)."""
    fp = _new_fp(
        "Molex_503480-0800_1x08-1MP_P0.50mm_Horizontal",
        "Molex 503480-0800 8-circuit 0.50 mm pitch BackFlip FPC receptacle",
    )
    for i in range(8):
        _smd(fp, i + 1, -1.75 + i * 0.50, -1.65, 0.30, 1.10, ratio=0.1)
    for sx in (-2.80, 2.80):
        _smd(fp, "MP", sx, 1.35, 0.85, 1.40, ratio=0.1)
    _rect(fp, pcbnew.F_Fab, -3.15, -1.90, 3.15, 1.90, 0.1)
    # Silkscreen outline kept clear of pads
    _line(fp, pcbnew.F_SilkS, -3.35, -1.90, -3.35, 0.45, 0.12)
    _line(fp, pcbnew.F_SilkS, 3.35, -1.90, 3.35, 0.45, 0.12)
    _line(fp, pcbnew.F_SilkS, -2.15, 2.05, 2.15, 2.05, 0.12)
    _line(fp, pcbnew.F_SilkS, -1.75, -2.45, -1.75, -2.75, 0.25)  # pin 1 marker
    _rect(fp, pcbnew.F_CrtYd, -3.55, -2.50, 3.55, 2.35, 0.05)
    fp.Reference().SetPosition(_v(0, -3.3))
    fp.Value().SetPosition(_v(0, 3.0))
    return fp


def fp_wurth_m3():
    """Wurth Elektronik WA-SMSI 9774030360R M3x3.0 mm SMT spacer (OD 7.4 mm pad, 4.4 mm hole)."""
    fp = _new_fp(
        "Wurth_9774030360R_M3",
        "Wurth 9774030360R WA-SMSI M3x3.0 mm SMD threaded spacer (7.4 mm pad, 4.4 mm hole)",
        attr=pcbnew.FP_THROUGH_HOLE,
    )
    _pth(fp, 1, 0, 0, 7.4, 4.4, "circle")
    _circle(fp, pcbnew.F_Fab, 3.0, 0.1)
    _circle(fp, pcbnew.F_SilkS, 3.9, 0.12)
    _circle(fp, pcbnew.F_CrtYd, 4.2, 0.05)
    fp.Reference().SetPosition(_v(0, -4.8))
    fp.Value().SetPosition(_v(0, 4.8))
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


def main():
    LIB.mkdir(exist_ok=True)
    SYM.write_text(symbols(), encoding="utf8", newline="\n")
    if PRETTY.exists():
        shutil.rmtree(PRETTY)
    PRETTY.mkdir()
    io = pcbnew.PCB_IO_KICAD_SEXPR()
    for f in (fp_molex_503480(), fp_wurth_m3(), fp_jz100()):
        io.FootprintSave(str(PRETTY), f)
    print("wrote", SYM, "and", sorted(os.listdir(PRETTY)))


if __name__ == "__main__":
    main()
