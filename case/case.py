"""Parametric two-piece build123d pen enclosure for ProbePen-AI.

Driven by hw/out/board_features.json (written by hw/gen_pcb.py) and the board outline in hardware/pcb_datum.py.
CAD frame: origin at the nose tip on the board centre line, +X towards the USB-C tail, +Z up out of F.Cu.
The PCB spans Z = 0 (B.Cu) .. 1.6 (F.Cu); the shells split at the PCB top face.

What the case does, part by part:
  - Outline follows the board: 14 mm nose, S-curve taper (taper_x), 17.6 mm body, rounded corners.
  - Nose: a front window over the WAGO 2060 wire entries and the two B-side work-light LEDs, and a window in
    the top over the terminal's release push-buttons.
  - LCD: mounted on B.Cu, so the viewing window is in the bottom shell floor, with a ledge under the glass rim.
  - Buttons: four TS24CA side-actuated switches whose plungers stick out of the -Y board edge. Each gets a
    flexure tab in the top shell's -Y wall (two vertical slots, thinned outside, a pocket inside over the plunger).
  - Trimmers CT1/CT2 (B side): screwdriver holes in the bottom floor.
  - J3 pod connector (+Y side) and J1 USB-C (tail): cut-outs from their plug envelopes.
  - Two M2 screws self-tap into 1.7 mm pilots in the bottom bosses (no heat-set inserts).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from build123d import (
    Axis,
    Box,
    Compound,
    Cylinder,
    Edge,
    Face,
    Location,
    Part,
    Plane,
    Vector,
    Wire,
    chamfer,
    export_step,
    export_stl,
    extrude,
    fillet,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hardware.pcb_datum import half_width_at  # noqa: E402

FEATURES_FILE = ROOT / "hw" / "out" / "board_features.json"
CASE_OUT = ROOT / "case" / "out"

PCB_CLR_XY = 0.35
WALL_THICK = 1.60
CAVITY_BOT = 2.60       # clearance below PCB bottom (Z = 0): LCD glass reaches Z = -1.8
CAVITY_TOP = 4.80       # clearance above PCB top (Z = 1.6): the WAGO 2060 is 4.5 mm tall
Z_SPLIT = 1.60          # clamshell split at PCB top surface
EDGE_ROUND = 1.10       # outer edge fillet

M2_PILOT_DIAM_MM = 1.70  # self-tapping M2 pilot directly in plastic (no inserts)
M2_CLEAR_DIAM_MM = 2.20
M2_CBORE_DIAM_MM = 4.20
M2_BOSS_DIAM_MM = 4.60
M2_SCREW_LEN_MM = 8.0    # pan head, from the counterbore floor down into the bottom boss
M2_CBORE_FLOOR_Z = 4.50  # screw head seat in the top boss (head ~1.6 mm, stays below the surface)
M2_PILOT_BOTTOM_Z = -3.75

# WAGO 2060-453 on the nose (footprint frame = CAD frame, origin at the J5 position)
WAGO_POLE_Y = (-4.0, 0.0, 4.0)
WAGO_PUSHER_X = (0.0, 6.1)     # top release push-buttons, relative to J5 x
WAGO_PUSHER_HALF_Y = 5.5
WIRE_ENTRY_Z = Z_SPLIT + 2.0   # wire entry height (centre) above the board

# TS24CA side buttons (plunger 2.0 mm wide, tip 2.425 mm from the switch centre, body 1.9 mm tall)
BTN_PLUNGER_HALF_X = 1.0
BTN_PLUNGER_REACH = 2.425
BTN_BODY_H = 1.9
TAB_HALF_X = 2.4         # flexure tab half-width
TAB_SLOT = 0.6           # slot width either side of the tab
TAB_TOP_Z = Z_SPLIT + 4.4
TAB_GAP_Z = 0.3          # gap between tab bottom and the bottom shell
TAB_THIN = 0.4           # outside recess, leaves a 1.2 mm tab
POCKET_HALF_X = 1.4
POCKET_GAP = 0.25        # plunger tip to tab inner face at rest

TRIM_HOLE_DIAM_MM = 2.2
LCD_LEDGE_GAP = 0.05


def load_features(path: Path = FEATURES_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf8"))


def _pen_face(feat: dict, grow: float) -> Face:
    """Board outline grown outward by `grow` mm: nose corners, S-curve taper (splines), straight body, tail corners."""
    length = feat["outline_mm"][0]
    hn = feat["nose_width"] / 2.0
    hb = feat["outline_mm"][1] / 2.0
    rn, rt = feat["nose_r"], feat["tail_r"]
    tx0, tx1 = feat["taper_x"]
    g = grow

    def arc(cx, cy, r, a0, a1):
        return Edge.make_circle(r, Plane(origin=(cx, cy, 0)), start_angle=a0, end_angle=a1)

    def line(p, q):
        return Edge.make_line(Vector(*p, 0), Vector(*q, 0))

    xs = [tx0 + (tx1 - tx0) * i / 24 for i in range(25)]
    lower = [Vector(x, -half_width_at(x, margin=-g), 0) for x in xs]
    upper = [Vector(x, half_width_at(x, margin=-g), 0) for x in reversed(xs)]
    edges = [
        line((-g, hn - rn), (-g, -(hn - rn))),
        arc(rn, -(hn - rn), rn + g, 180, 270),
        line((rn, -(hn + g)), (tx0, -(hn + g))),
        Edge.make_spline(lower, tangents=[Vector(1, 0, 0), Vector(1, 0, 0)]),
        line((tx1, -(hb + g)), (length - rt, -(hb + g))),
        arc(length - rt, -(hb - rt), rt + g, 270, 360),
        line((length + g, -(hb - rt)), (length + g, hb - rt)),
        arc(length - rt, hb - rt, rt + g, 0, 90),
        line((length - rt, hb + g), (tx1, hb + g)),
        Edge.make_spline(upper, tangents=[Vector(-1, 0, 0), Vector(-1, 0, 0)]),
        line((tx0, hn + g), (rn, hn + g)),
        arc(rn, hn - rn, rn + g, 90, 180),
    ]
    return Face(Wire(edges))


def _pen_prism(feat: dict, grow: float, z0: float, z1: float) -> Part:
    return Part(extrude(_pen_face(feat, grow), amount=z1 - z0)).locate(Location((0.0, 0.0, z0)))


def _slot_box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Part:
    """Axis-aligned box spanning exact corners [x0..x1] x [y0..y1] x [z0..z1]."""
    cx, cy, cz = 0.5 * (x0 + x1), 0.5 * (y0 + y1), 0.5 * (z0 + z1)
    return Location((cx, cy, cz)) * Box(abs(x1 - x0), abs(y1 - y0), abs(z1 - z0))


def _zcyl(x: float, y: float, z0: float, z1: float, d: float) -> Part:
    return Location((x, y, 0.5 * (z0 + z1))) * Cylinder(radius=d / 2.0, height=z1 - z0)


def build_pcb_solid(feat: dict | None = None) -> Part:
    """Solid model of the bare PCB slab minus M2 mounting holes."""
    if feat is None:
        feat = load_features()
    thk = feat["thickness"]
    pcb = _pen_prism(feat, 0.0, 0.0, thk)
    for hx, hy, hd in feat["holes"]:
        pcb -= _zcyl(hx, hy, -1.0, thk + 1.0, hd)
    return pcb


def button_plungers(feat: dict) -> list[Part]:
    """Envelope of each TS24CA plunger beyond the switch body (CAD -Y side)."""
    out = []
    for bx, by in feat["buttons"]:
        tip = by - BTN_PLUNGER_REACH
        out.append(_slot_box(bx - BTN_PLUNGER_HALF_X, tip, Z_SPLIT + 0.3, bx + BTN_PLUNGER_HALF_X, by - 1.225,
                             Z_SPLIT + BTN_BODY_H - 0.3))
    return out


def build_parts(feat: dict | None = None) -> dict[str, Part]:
    """Construct parametric top_shell, bottom_shell, and pcb solid in CAD coordinates."""
    if feat is None:
        feat = load_features()

    length, width = feat["outline_mm"]
    thk = feat["thickness"]
    hb = width / 2.0
    in_hy = hb + PCB_CLR_XY
    out_hy = in_hy + WALL_THICK
    in_x0 = -PCB_CLR_XY
    out_x0, out_x1 = in_x0 - WALL_THICK, length + PCB_CLR_XY + WALL_THICK

    z_bot_in = -CAVITY_BOT
    z_bot_out = z_bot_in - WALL_THICK
    z_top_in = thk + CAVITY_TOP
    z_top_out = z_top_in + WALL_THICK
    g_in, g_out = PCB_CLR_XY, PCB_CLR_XY + WALL_THICK

    bot_blank = _pen_prism(feat, g_out, z_bot_out, Z_SPLIT)
    top_blank = _pen_prism(feat, g_out, Z_SPLIT, z_top_out)
    bot_blank = fillet(bot_blank.faces().sort_by(Axis.Z)[0].edges(), radius=EDGE_ROUND)
    top_blank = fillet(top_blank.faces().sort_by(Axis.Z)[-1].edges(), radius=EDGE_ROUND)

    bot_shell = bot_blank - _pen_prism(feat, g_in, z_bot_in, Z_SPLIT + 0.5)
    top_shell = top_blank - _pen_prism(feat, g_in, Z_SPLIT - 0.5, z_top_in)

    # M2 self-tapping bosses at H1 / H2 (no heat-set inserts): pilot in the bottom, clearance + counterbore on top
    for hx, hy, _ in feat["holes"]:
        bot_shell = bot_shell + _zcyl(hx, hy, z_bot_in - 0.1, 0.0, M2_BOSS_DIAM_MM)
        bot_shell = bot_shell - _zcyl(hx, hy, M2_PILOT_BOTTOM_Z, 0.1, M2_PILOT_DIAM_MM)
        top_shell = top_shell + _zcyl(hx, hy, thk, z_top_in + 0.1, M2_BOSS_DIAM_MM)
        top_shell = top_shell - _zcyl(hx, hy, Z_SPLIT - 1.0, z_top_out + 1.0, M2_CLEAR_DIAM_MM)
        top_shell = top_shell - _zcyl(hx, hy, M2_CBORE_FLOOR_Z, z_top_out + 0.5, M2_CBORE_DIAM_MM)

    cons = {c["ref"]: c for c in feat["connectors"]}

    # Nose: front window over the WAGO wire entries and the B-side work-light LEDs
    j5 = cons["J5"]
    env = j5["plug_envelope"]
    nose_cut = _slot_box(out_x0 - 2.0, env[1], env[2], in_x0 + 0.5, env[4], env[5])
    bot_shell -= nose_cut
    top_shell -= nose_cut
    # ... and a top window over the terminal's release push-buttons
    jx = feat["part_centres"]["J5"][0]
    top_shell -= _slot_box(jx + WAGO_PUSHER_X[0] - 0.4, -WAGO_PUSHER_HALF_Y, z_top_in - 1.0,
                           jx + WAGO_PUSHER_X[1] + 0.4, WAGO_PUSHER_HALF_Y, z_top_out + 1.0)

    # LCD on B.Cu: viewing window in the bottom floor with an outer chamfer, ledge under the glass rim
    lx0, ly0, lx1, ly1 = feat["lcd_window"]
    gx0, gy0, gx1, gy1 = feat["lcd_glass"]
    glass_z = cons["J4"]["plug_envelope"][2]
    ledge = _slot_box(gx0 + 0.3, -in_hy - 0.2, z_bot_in - 0.1, gx1 - 0.3, in_hy + 0.2, glass_z - LCD_LEDGE_GAP)
    ledge -= _slot_box(lx0, ly0, z_bot_in - 1.0, lx1, ly1, 0.0)
    bot_shell += ledge
    win = _slot_box(lx0, ly0, z_bot_out - 1.0, lx1, ly1, z_bot_in + 0.5)
    win = fillet(win.edges().filter_by(Axis.Z), radius=0.6)
    bot_shell -= win
    bezel = _slot_box(lx0 - 0.4, ly0 - 0.4, z_bot_out - 0.5, lx1 + 0.4, ly1 + 0.4, z_bot_out + 0.45)
    bot_shell -= chamfer(bezel.faces().sort_by(Axis.Z)[-1].edges(), length=0.35)

    # Trimmer capacitors CT1/CT2 on B.Cu: screwdriver holes in the bottom floor
    for ref in ("CT1", "CT2"):
        tx, ty = feat["part_centres"][ref]
        bot_shell -= _zcyl(tx, ty, z_bot_out - 1.0, z_bot_in + 0.5, TRIM_HOLE_DIAM_MM)

    # Side buttons: a flexure tab in the top shell's -Y wall over each plunger
    y_out, y_in = -out_hy - 1.0, -in_hy + 0.3
    for bx, by in feat["buttons"]:
        for sx in (-1, 1):
            xc = bx + sx * (TAB_HALF_X + TAB_SLOT / 2.0)
            top_shell -= _slot_box(xc - TAB_SLOT / 2.0, y_out, Z_SPLIT - 0.5, xc + TAB_SLOT / 2.0, y_in, TAB_TOP_Z)
        top_shell -= _slot_box(bx - TAB_HALF_X - TAB_SLOT, y_out, Z_SPLIT - 0.5,
                               bx + TAB_HALF_X + TAB_SLOT, y_in, Z_SPLIT + TAB_GAP_Z)
        top_shell -= _slot_box(bx - TAB_HALF_X + 0.2, y_out, Z_SPLIT, bx + TAB_HALF_X - 0.2, -out_hy + TAB_THIN,
                               TAB_TOP_Z - 0.4)
        tip = by - BTN_PLUNGER_REACH
        top_shell -= _slot_box(bx - POCKET_HALF_X, tip - POCKET_GAP, Z_SPLIT, bx + POCKET_HALF_X, -in_hy + 0.5,
                               Z_SPLIT + BTN_BODY_H + 0.4)

    # J3 side JST-SH pod cut-out (+Y wall) & J1 USB-C tail cut-out (+X tail)
    j3_env = cons["J3"]["plug_envelope"]
    j3_cut = _slot_box(j3_env[0], in_hy - 0.5, j3_env[2], j3_env[3], out_hy + 2.0, j3_env[5])
    top_shell -= j3_cut
    bot_shell -= j3_cut

    j1_env = cons["J1"]["plug_envelope"]
    j1_cut = _slot_box(length - 2.0, j1_env[1], j1_env[2], out_x1 + 2.0, j1_env[4], j1_env[5])
    top_shell -= j1_cut
    bot_shell -= j1_cut

    pcb_solid = build_pcb_solid(feat)
    return {"top_shell": top_shell, "bottom_shell": bot_shell, "pcb": pcb_solid}


def build_and_export(out_dir: Path = CASE_OUT, feat_path: Path = FEATURES_FILE) -> dict[str, Part]:
    out_dir.mkdir(parents=True, exist_ok=True)
    feat = load_features(feat_path)
    parts = build_parts(feat)

    for name in ("top_shell", "bottom_shell"):
        part = parts[name]
        export_step(part, str(out_dir / f"{name}.step"))
        export_stl(part, str(out_dir / f"{name}.stl"))

    asm = Compound(label="ProbePen_AI_Assembly", children=[parts["top_shell"], parts["bottom_shell"], parts["pcb"]])
    export_step(asm, str(out_dir / "assembly.step"))
    return parts


def main() -> None:
    parts = build_and_export()
    for name, p in parts.items():
        bb = p.bounding_box()
        print(
            f"{name:12s}: valid={p.is_valid} solids={len(p.solids())} vol={p.volume:.1f} mm^3 "
            f"bbox=[{bb.min.X:.1f}..{bb.max.X:.1f}]x[{bb.min.Y:.1f}..{bb.max.Y:.1f}]x[{bb.min.Z:.1f}..{bb.max.Z:.1f}]"
        )


if __name__ == "__main__":
    main()
