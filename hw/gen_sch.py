"""Write hw/probepen_ai.kicad_sch and hw/probepen_ai.kicad_pro from hw/design.py.

One A1 sheet. Components are laid out in functional blocks; every connected pin gets a short wire
and a local label with its net name, and NC/unconnected pins get a no-connect flag.
KiCad's ERC and netlist export verify the output (tests/test_sch.py).

    python -m hw.gen_sch
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from hw import design

HERE = Path(__file__).resolve().parent
SCH = HERE / "probepen_ai.kicad_sch"
PROJECT = "probepen_ai"
LOCAL = {"ppa": HERE / "lib" / "ppa.kicad_sym"}
G = 1.27  # 50 mil schematic grid

_DEFAULT_SYM_DIRS = (
    Path.home() / ".local" / "share" / "kicad" / "10.0" / "symbols",
    Path("/usr/share/kicad/symbols"),
)


def _find_kicad_symbols() -> Path:
    env = os.environ.get("KICAD_SYMBOL_DIR")
    if env:
        return Path(env)
    for d in _DEFAULT_SYM_DIRS:
        if d.is_dir():
            return d
    return _DEFAULT_SYM_DIRS[-1]


KICAD_SYMBOLS = _find_kicad_symbols()


def _uid(*parts) -> str:
    return str(uuid.uuid5(design.NS, "/".join(map(str, parts))))


def _snap(v: float) -> float:
    return round(round(v / G) * G, 4)


# ---- symbol library access -------------------------------------------------------------------
_lib_cache: dict[str, str] = {}


def _lib_text(lib: str) -> str:
    if lib not in _lib_cache:
        path = LOCAL.get(lib, KICAD_SYMBOLS / f"{lib}.kicad_sym")
        _lib_cache[lib] = path.read_text(encoding="utf8")
    return _lib_cache[lib]


def _block(text: str, start: int) -> str:
    depth = 0
    for j in range(start, len(text)):
        ch = text[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    raise ValueError("unbalanced S-expression")


def _raw_symbol(lib: str, name: str) -> str:
    text = _lib_text(lib)
    m = re.search(r'\n\s*\(symbol "' + re.escape(name) + r'"\s', text)
    if not m:
        raise KeyError(f"{lib}:{name}")
    return _block(text, m.start() + text[m.start():].index("("))


def _prop_block(sym: str, prop: str) -> str | None:
    m = re.search(r'\(property "' + re.escape(prop) + r'"', sym)
    return _block(sym, m.start()) if m else None


def flat_symbol(lib_id: str) -> str:
    """Symbol text with `extends` resolved, top-level name 'lib:name' for schematic lib_symbols."""
    lib, name = lib_id.split(":")
    sym = _raw_symbol(lib, name)
    ext = re.search(r'\(extends "([^"]+)"\)', sym)
    if ext:
        parent = ext.group(1)
        base = _raw_symbol(lib, parent)
        base = base.replace(f'(symbol "{parent}"', f'(symbol "{name}"', 1)
        base = re.sub(r'\(symbol "' + re.escape(parent) + r'_(\d+)_(\d+)"', rf'(symbol "{name}_\1_\2"', base)
        for prop in re.findall(r'\(property "([^"]+)"', sym):
            new, old = _prop_block(sym, prop), _prop_block(base, prop)
            if old:
                base = base.replace(old, new, 1)
            else:
                i = base.index("(property")
                base = base[:i] + new + "\n\t\t" + base[i:]
        sym = base
    return sym.replace(f'(symbol "{name}"', f'(symbol "{lib_id}"', 1)


def symbol_pins(lib_id: str) -> list[tuple[str, float, float, int]]:
    """(number, x, y, angle) for every pin of unit 1 / body style 1 (symbol coordinates, y up)."""
    sym = flat_symbol(lib_id)
    name = lib_id.split(":")[1]
    pins = []
    for m in re.finditer(r'\(symbol "' + re.escape(name) + r'_(\d+)_(\d+)"', sym):
        unit, style = int(m.group(1)), int(m.group(2))
        if unit not in (0, 1) or style not in (0, 1):
            continue
        sub = _block(sym, m.start())
        for pm in re.finditer(r"\(pin \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+) (\d+)\)", sub):
            pblock = _block(sub, pm.start())
            num = re.search(r'\(number "([^"]*)"', pblock).group(1)
            pins.append((num, float(pm.group(1)), float(pm.group(2)), int(pm.group(3))))
    return pins


# ---- layout ----------------------------------------------------------------------------------
# A1 sheet (841 x 594 mm): 3 columns of functional blocks with plenty of breathing room
BLOCKS = [  # (block, origin x, origin y, width)
    ("usb", 25.4, 30.48, 220.0),
    ("power", 25.4, 120.0, 220.0),
    ("ui", 25.4, 320.0, 220.0),
    ("lcd", 25.4, 420.0, 220.0),
    ("mcu", 270.0, 30.48, 260.0),
    ("afe", 560.0, 30.48, 265.0),
    ("adc", 560.0, 290.0, 265.0),
    ("logic", 560.0, 470.0, 265.0),
]
OUTWARD = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}  # schematic coords (y down)
STUB = 2.54


def _field_box(fields):
    xs, ys = [], []
    for fx, fy, ang, just, text in fields:
        w, h = len(text) * 1.0, 1.6
        if ang in (90, 270):
            w, h = h, w
        x0 = fx if "left" in just else fx - w if "right" in just else fx - w / 2
        xs += [x0, x0 + w]
        ys += [-fy - h / 2, -fy + h / 2]
    return xs, ys


def _bbox(pins, nets_by_pin, fields=()):
    xs, ys = _field_box(fields)
    xs, ys = xs + [0.0], ys + [0.0]
    for num, x, y, ang in pins:
        dx, dy = OUTWARD[ang]
        net = nets_by_pin.get(num, "")
        label_str = "" if net == "NC" else net
        reach = STUB + (len(label_str) * 1.0 + 1.5 if dx else 1.5)
        xs += [x, x + dx * reach]
        ys += [-y, -y + dy * (STUB + (len(label_str) * 1.0 + 1.5 if dy else 0))]
    x0, x1 = min(xs) - 2.54, max(xs) + 2.54
    if x1 - x0 < 12.7 and not fields:
        x0, x1 = -6.35, 6.35
    return x0, min(ys) - 5.08, x1, max(ys) + 5.08


def layout(items):
    """items: list of (key, block, pins, nets_by_pin, fields) -> {key: (x, y)}"""
    pos = {}
    for block, ox, oy, width in BLOCKS:
        x, y, row_h = ox, oy, 0.0
        for key, b, pins, nets, fields in items:
            if b != block:
                continue
            x0, y0, x1, y1 = _bbox(pins, nets, fields)
            w, h = x1 - x0, y1 - y0
            if x + w > ox + width and x > ox:
                x, y, row_h = ox, y + row_h, 0.0
            pos[key] = (_snap(x - x0), _snap(y - y0))
            x += w
            row_h = max(row_h, h)
    return pos


# ---- writers ---------------------------------------------------------------------------------
_FIELDS = {}


def lib_fields(lib_id):
    if lib_id not in _FIELDS:
        text = flat_symbol(lib_id)
        out = {}
        for m in re.finditer(r'\(property "(Reference|Value)" "[^"]*"\s*\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)', text):
            tail = text[m.end():text.find("(property", m.end())]
            j = re.search(r"\(justify ([a-z ]+)\)", tail)
            out[m.group(1)] = (
                float(m.group(2)), float(m.group(3)), float(m.group(4)),
                j.group(1) if j else "", "(hide yes)" in tail,
            )
        _FIELDS[lib_id] = out
    return _FIELDS[lib_id]


def _eff(hide=False, justify=""):
    j = f" (justify {justify})" if justify else ""
    h = " (hide yes)" if hide else ""
    return f"(effects (font (size 1.27 1.27)){j}{h})"


def _prop(name, value, x, y, hide=False, ang=0, justify=""):
    return f'\t\t(property "{name}" "{value}" (at {x} {y} {ang:g}) {_eff(hide, justify)})\n'


def place_symbol(ref, lib_id, value, footprint, lcsc, x, y, pins, dnp=False, extra_props=()):
    in_bom = "no" if (dnp or ref.startswith("#")) else "yes"
    s = (
        f'\t(symbol (lib_id "{lib_id}") (at {x} {y} 0) (unit 1)\n'
        f'\t\t(exclude_from_sim no) (in_bom {in_bom}) (on_board {"no" if ref.startswith("#") else "yes"})'
        f' (in_pos_files {in_bom}) (dnp {"yes" if dnp else "no"})\n'
        f'\t\t(uuid "{_uid("sym", ref)}")\n'
    )
    hide_ref = ref.startswith("#")
    hidden = {"Reference": hide_ref, "Value": hide_ref and lib_id != "power:PWR_FLAG"}
    fields = lib_fields(lib_id)
    for name, val, dy in (("Reference", ref, -3.81), ("Value", value, 3.81)):
        if name in fields:
            fx, fy, fa, fj, _ = fields[name]
            s += _prop(name, val, round(x + fx, 3), round(y - fy, 3), hide=hidden[name], ang=fa, justify=fj)
        else:
            s += _prop(name, val, x, _snap(y + dy), hide=hidden[name])
    s += _prop("Footprint", footprint, x, y, hide=True)
    s += _prop("Datasheet", "", x, y, hide=True)
    if lcsc:
        s += _prop("LCSC", lcsc, x, y, hide=True)
    for k, v in extra_props:
        s += _prop(k, v, x, y, hide=True)
    for num in dict.fromkeys(p[0] for p in pins):
        s += f'\t\t(pin "{num}" (uuid "{_uid("pin", ref, num)}"))\n'
    s += (
        f'\t\t(instances (project "{PROJECT}" (path "/{design.ROOT_UUID}" (reference "{ref}") (unit 1))))\n'
        "\t)\n"
    )
    return s


def wire(x0, y0, x1, y1, key):
    return (
        f"\t(wire (pts (xy {x0} {y0}) (xy {x1} {y1})) (stroke (width 0) (type default))"
        f' (uuid "{_uid("wire", key)}"))\n'
    )


def label(net, x, y, ang, key):
    just = {0: "left bottom", 180: "right bottom", 90: "left bottom", 270: "right bottom"}[ang]
    return f'\t(label "{net}" (at {x} {y} {ang}) {_eff(justify=just)} (uuid "{_uid("label", key)}"))\n'


def no_connect(x, y, key):
    return f'\t(no_connect (at {x} {y}) (uuid "{_uid("nc", key)}"))\n'


def connect_pins(ref, sx, sy, pins, nets_by_pin):
    out, done = [], {}
    for num, px, py, ang in pins:
        cx, cy = _snap(sx + px), _snap(sy - py)
        net = nets_by_pin.get(num)
        if net == "NC":
            net = None
        if (cx, cy) in done:
            if net and done[(cx, cy)] and net != done[(cx, cy)]:
                raise ValueError(f"{ref} stacked pins on different nets: {net} / {done[(cx, cy)]}")
            continue
        done[(cx, cy)] = net
        if not net:
            out.append(no_connect(cx, cy, f"{ref}.{num}"))
            continue
        dx, dy = OUTWARD[ang]
        ex, ey = _snap(cx + dx * STUB), _snap(cy + dy * STUB)
        out.append(wire(cx, cy, ex, ey, f"{ref}.{num}"))
        lang = {(-1, 0): 180, (1, 0): 0, (0, 1): 270, (0, -1): 90}[(dx, dy)]
        out.append(label(net, ex, ey, lang, f"{ref}.{num}"))
    return out


# Nets containing power_in pins with no power_out driver (fed via passive fuse/bead/inductor/diode)
PWR_FLAGS = [
    "+5V_VBUS",
    "+5V_ANA_IN",
    "+1V1_CORE",
    "VREG_AVDD",
    "ADC_AVDD",
    "GND",
    "AGND",
]


def build() -> str:
    comps = design.COMPONENTS
    lib_ids = sorted({c.symbol for c in comps} | {"power:PWR_FLAG"})
    pins_of = {lid: symbol_pins(lid) for lid in lib_ids}

    def fields(c):
        f = lib_fields(c.symbol)
        return [(*f[n][:4], t) for n, t in (("Reference", c.ref), ("Value", c.value)) if n in f]

    items = [(c.ref, c.block, pins_of[c.symbol], c.pins, fields(c)) for c in comps]
    items += [
        (f"#FLG{i + 1:02d}", "power", pins_of["power:PWR_FLAG"], {"1": net}, [])
        for i, net in enumerate(PWR_FLAGS)
    ]
    pos = layout(items)

    out = [
        f'(kicad_sch\n\t(version 20260306)\n\t(generator "ppa_gen_sch")\n\t(generator_version "10.0")\n'
        f'\t(uuid "{design.ROOT_UUID}")\n\t(paper "A1")\n'
        '\t(title_block (title "ProbePen-AI") (rev "1.0") (company "probepen-ai")\n'
        '\t\t(comment 1 "Generated by hw/gen_sch.py from hw/design.py - edit design.py, not this file")\n'
        '\t\t(comment 2 "RP2354B + AD9288 Dual 80 MSPS Scope + 3-Gain AFE + Logic Probe + 4-Ch Pod + ST7789/ST7735S LCD")\n\t)\n'
    ]
    out.append("\t(lib_symbols\n")
    for lid in lib_ids:
        out.append(flat_symbol(lid) + "\n")
    out.append("\t)\n")

    for c in comps:
        x, y = pos[c.ref]
        extra_props = [("MPN", design.PARTS[c.part].mpn)] if c.part else []
        out.append(place_symbol(
            c.ref, c.symbol, c.value, c.footprint, c.lcsc, x, y, pins_of[c.symbol],
            dnp=c.dnp, extra_props=extra_props,
        ))
        out += connect_pins(c.ref, x, y, pins_of[c.symbol], c.pins)

    for i, net in enumerate(PWR_FLAGS):
        ref = f"#FLG{i + 1:02d}"
        x, y = pos[ref]
        out.append(place_symbol(ref, "power:PWR_FLAG", "PWR_FLAG", "", "", x, y, pins_of["power:PWR_FLAG"]))
        out += connect_pins(ref, x, y, pins_of["power:PWR_FLAG"], {"1": net})

    titles = {
        "usb": "USB-C, 500 mA PTC, CC pulldowns, SRV05-4 ESD",
        "power": "Power Tree: +3V3_DIG, +3V3_ANA, +3V0_REF, Star GND, Test Pads",
        "mcu": "RP2354B QFN-80, 1.1 V Buck, 12 MHz XTAL, BOOTSEL, RUN, SWD",
        "afe": "Dual-Channel 2-Gain Single-Supply AFE: 1M Dividers, BAV199, Dual RS8752XM, 74LVC2G66DP",
        "adc": "Dual High-Speed ADC: AD9288BSTZ-80 LQFP-48 (2x 8-bit 80 MSPS, Single-Ended 1.024 Vpp)",
        "logic": "6-Pin Bidirectional GPIO Expansion Header (JST-SH, 500 mA PTC, 220R, SRV05-4 ESD)",
        "lcd": "1.14in/0.96in IPS LCD (13P 0.7mm Solder FPC + AO3401A Backlight PWM)",
        "ui": "User Interface: 4x Side Buttons (AI, MODE/BOOTSEL, UP, DOWN) + 2x XL-3806UWC Front Worklight LEDs",
    }
    for block, ox, oy, _ in BLOCKS:
        out.append(
            f'\t(text "{titles[block]}" (exclude_from_sim no) (at {_snap(ox)} {_snap(oy - 7.62)} 0)'
            f' (effects (font (size 2.54 2.54) (bold yes)) (justify left bottom))'
            f' (uuid "{_uid("title", block)}"))\n'
        )
    out.append('\t(sheet_instances (path "/" (page "1")))\n\t(embedded_fonts no)\n)\n')
    return "".join(out)


NETCLASS_RULES = {  # class: (track width, clearance within the class), mm
    "Default": (0.2, 0.092),
    "LV": (0.2, 0.092),
    "POWER": (0.4, 0.092),
    "ANALOG": (0.2, 0.092),
    "USB": (0.25, 0.092),
    "HIGHSPEED": (0.15, 0.092),
}


def write_project():
    """hw/probepen_ai.kicad_pro: net classes and JLCPCB 4-layer board limits."""
    classes = []
    for i, (name, (w, c)) in enumerate(NETCLASS_RULES.items()):
        classes.append({
            "name": name,
            "clearance": c,
            "track_width": w,
            "via_diameter": 0.45,
            "via_drill": 0.25,
            "microvia_diameter": 0.3,
            "microvia_drill": 0.1,
            "diff_pair_width": 0.25,
            "diff_pair_gap": 0.15,
            "diff_pair_via_gap": 0.25,
            "wire_width": 6,
            "bus_width": 12,
            "line_style": 0,
            "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)",
            "priority": 2147483647 if name == "Default" else i,
        })
    patterns = [{"netclass": cls, "pattern": "/" + net} for net, cls in sorted(design.NETCLASS.items())]
    proj = {
        "meta": {"filename": f"{PROJECT}.kicad_pro", "version": 3},
        "board": {
            "design_settings": {
                "rules": {
                    "min_clearance": 0.092,
                    "min_copper_edge_clearance": 0.3,
                    "min_hole_clearance": 0.25,
                    "min_hole_to_hole": 0.25,
                    "min_through_hole_diameter": 0.2,
                    "min_track_width": 0.092,
                    "min_via_annular_width": 0.1,
                    "min_via_diameter": 0.40,
                    "min_silk_clearance": 0.0,
                    "solder_mask_to_copper_clearance": 0.0,
                    "use_height_for_length_calcs": True,
                },
                "rule_severities": {"hole_to_hole": "error"},
                "track_widths": [0.0, 0.15, 0.2, 0.25, 0.4, 0.6],
                "via_dimensions": [
                    {"diameter": 0.0, "drill": 0.0},
                    {"diameter": 0.45, "drill": 0.25},
                    {"diameter": 0.6, "drill": 0.3},
                ],
            }
        },
        "net_settings": {
            "classes": classes,
            "meta": {"version": 5},
            "net_colors": {},
            "netclass_assignments": {},
            "netclass_patterns": patterns,
        },
        "sheets": [[design.ROOT_UUID, "Root"]],
        "text_variables": {},
    }
    (HERE / f"{PROJECT}.kicad_pro").write_text(json.dumps(proj, indent=2) + "\n", encoding="utf8", newline="\n")


def main():
    write_project()
    SCH.write_text(build(), encoding="utf8", newline="\n")
    print("wrote", SCH)


if __name__ == "__main__":
    main()
