"""Parametric two-piece build123d pen enclosure for ProbePen-AI.

Driven directly by hw/out/board_features.json and hardware/pcb_datum.py.
Uses M2 self-tapping fasteners directly into 1.7 mm bottom plastic pilot holes
(no heat-set inserts).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from build123d import (
    Axis,
    Box,
    Compound,
    Cylinder,
    Location,
    Part,
    Rectangle,
    chamfer,
    export_step,
    export_stl,
    extrude,
    fillet,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FEATURES_FILE = ROOT / "hw" / "out" / "board_features.json"
CASE_OUT = ROOT / "case" / "out"

PCB_CLR_XY = 0.35
WALL_THICK = 1.60
CAVITY_BOT = 2.60       # clearance below PCB bottom (Z = 0)
CAVITY_TOP = 4.20       # clearance above PCB top (Z = 1.6)
Z_SPLIT = 1.60          # clamshell split at PCB top surface

M2_PILOT_DIAM_MM = 1.70  # self-tapping M2 pilot directly in plastic (no inserts)
M2_CLEAR_DIAM_MM = 2.20
M2_CBORE_DIAM_MM = 4.20
M2_BOSS_DIAM_MM = 4.60

NOSE_BORE_DIAM_MM = 4.20
BTN_HOLE_DIAM_MM = 3.80
LED_HOLE_DIAM_MM = 2.00


def load_features(path: Path = FEATURES_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf8"))


def _rounded_pen_prism(
    x0: float,
    x1: float,
    half_y: float,
    r_nose: float,
    r_tail: float,
    z0: float,
    z1: float,
) -> Part:
    """Create a pen-profile prism in XY spanning [x0..x1], [-half_y..+half_y], [z0..z1]."""
    cx = 0.5 * (x0 + x1)
    sk = Location((cx, 0.0, 0.0)) * Rectangle(x1 - x0, 2.0 * half_y)
    nose_verts = [v for v in sk.vertices() if v.X < cx]
    tail_verts = [v for v in sk.vertices() if v.X > cx]
    if r_nose > 0.05:
        sk = fillet(nose_verts, radius=min(r_nose, half_y - 0.05))
    if r_tail > 0.05:
        sk = fillet(tail_verts, radius=min(r_tail, half_y - 0.05))
    solid = extrude(sk, amount=z1 - z0)
    return solid.locate(Location((0.0, 0.0, z0)))


def _slot_box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Part:
    """Axis-aligned box spanning exact corners [x0..x1] x [y0..y1] x [z0..z1]."""
    cx, cy, cz = 0.5 * (x0 + x1), 0.5 * (y0 + y1), 0.5 * (z0 + z1)
    return Location((cx, cy, cz)) * Box(x1 - x0, y1 - y0, z1 - z0)


def build_pcb_solid(feat: dict | None = None) -> Part:
    """Solid model of the bare PCB slab minus M2 mounting holes."""
    if feat is None:
        feat = load_features()
    length, width = feat["outline_mm"]
    thk = feat["thickness"]
    pcb = _rounded_pen_prism(0.0, length, width / 2.0, feat["nose_r"], feat["tail_r"], 0.0, thk)
    for hx, hy, hd in feat["holes"]:
        pcb -= Location((hx, hy, thk / 2.0)) * Cylinder(radius=hd / 2.0, height=thk + 2.0)
    return pcb


def build_parts(feat: dict | None = None) -> dict[str, Part]:
    """Construct parametric top_shell, bottom_shell, and pcb solid in CAD coordinates."""
    if feat is None:
        feat = load_features()

    length, width = feat["outline_mm"]
    thk = feat["thickness"]
    hw_y = width / 2.0
    nose_r = feat["nose_r"]
    tail_r = feat["tail_r"]

    in_x0, in_x1 = -PCB_CLR_XY, length + PCB_CLR_XY
    in_hy = hw_y + PCB_CLR_XY
    out_x0, out_x1 = in_x0 - WALL_THICK, in_x1 + WALL_THICK
    out_hy = in_hy + WALL_THICK

    z_bot_in = -CAVITY_BOT
    z_bot_out = z_bot_in - WALL_THICK
    z_top_in = thk + CAVITY_TOP
    z_top_out = z_top_in + WALL_THICK

    r_nose_in = nose_r + PCB_CLR_XY
    r_tail_in = tail_r + PCB_CLR_XY
    r_nose_out = r_nose_in + WALL_THICK
    r_tail_out = r_tail_in + WALL_THICK

    # Outer blanks split cleanly at Z = 1.60 mm (PCB top plane)
    bot_blank = _rounded_pen_prism(out_x0, out_x1, out_hy, r_nose_out, r_tail_out, z_bot_out, Z_SPLIT)
    top_blank = _rounded_pen_prism(out_x0, out_x1, out_hy, r_nose_out, r_tail_out, Z_SPLIT, z_top_out)

    # Ergonomic exterior edge rounding
    bot_face_edges = bot_blank.faces().sort_by(Axis.Z)[0].edges()
    bot_blank = fillet(bot_face_edges, radius=1.10)
    top_face_edges = top_blank.faces().sort_by(Axis.Z)[-1].edges()
    top_blank = fillet(top_face_edges, radius=1.10)

    # Interior board + component cavities
    bot_cavity = _rounded_pen_prism(in_x0, in_x1, in_hy, r_nose_in, r_tail_in, z_bot_in, Z_SPLIT + 0.5)
    top_cavity = _rounded_pen_prism(in_x0, in_x1, in_hy, r_nose_in, r_tail_in, Z_SPLIT - 0.5, z_top_in)

    bot_shell = bot_blank - bot_cavity
    top_shell = top_blank - top_cavity

    # M2 self-tapping fastener bosses aligned with H1 & H2 (NO heat-set inserts)
    for hx, hy, _ in feat["holes"]:
        bot_boss = Location((hx, hy, 0.5 * z_bot_in)) * Cylinder(
            radius=M2_BOSS_DIAM_MM / 2.0, height=CAVITY_BOT,
        )
        bot_pilot = Location((hx, hy, -1.60)) * Cylinder(
            radius=M2_PILOT_DIAM_MM / 2.0, height=3.20,
        )
        bot_shell = (bot_shell + bot_boss) - bot_pilot

        top_boss = Location((hx, hy, 0.5 * (Z_SPLIT + z_top_in))) * Cylinder(
            radius=M2_BOSS_DIAM_MM / 2.0, height=CAVITY_TOP,
        )
        top_thru = Location((hx, hy, 0.5 * (Z_SPLIT + z_top_out))) * Cylinder(
            radius=M2_CLEAR_DIAM_MM / 2.0, height=z_top_out - Z_SPLIT + 2.0,
        )
        top_cbore = Location((hx, hy, z_top_out - 0.80)) * Cylinder(
            radius=M2_CBORE_DIAM_MM / 2.0, height=2.40,
        )
        top_shell = (top_shell + top_boss) - top_thru - top_cbore

    # Nose tip aperture (J2 M3 stainless probe tip) centered on Z_SPLIT
    nose_bore = Location((0.0, 0.0, Z_SPLIT)) * (
        Cylinder(radius=NOSE_BORE_DIAM_MM / 2.0, height=12.0).rotate(Axis.Y, 90)
    )
    bot_shell -= nose_bore
    top_shell -= nose_bore

    # LCD viewing window in top shell ceiling (rounded rectangle + outer bezel chamfer)
    lx0, ly0, lx1, ly1 = feat["lcd_window"]
    lcd_cut = _rounded_pen_prism(lx0, lx1, 0.5 * (ly1 - ly0), 0.6, 0.6, z_top_in - 1.0, z_top_out + 1.0)
    top_shell -= lcd_cut
    lcd_bezel = _slot_box(lx0 - 0.4, ly0 - 0.4, z_top_out - 0.45, lx1 + 0.4, ly1 + 0.4, z_top_out + 0.5)
    top_shell -= chamfer(lcd_bezel.faces().sort_by(Axis.Z)[0].edges(), length=0.35)

    # SW1 / SW2 tactile button apertures on top shell
    for bx, by in feat["buttons"]:
        top_shell -= Location((bx, by, z_top_in)) * Cylinder(
            radius=BTN_HOLE_DIAM_MM / 2.0, height=WALL_THICK + 3.0,
        )

    # Dual WS2812B status LED light-pipe holes on top shell
    for dx, dy in feat["leds"]:
        top_shell -= Location((dx, dy, z_top_in)) * Cylinder(
            radius=LED_HOLE_DIAM_MM / 2.0, height=WALL_THICK + 3.0,
        )

    # J3 side JST-SH pod cutout (+Y wall) & J1 USB-C tail plug cutout (+X tail)
    cons = {c["ref"]: c for c in feat["connectors"]}
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
