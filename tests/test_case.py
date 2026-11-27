"""Stage 5 mechanical enclosure checks: watertight solids, dimensions, PCB clearance, openings, and self-tapping pilot holes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from build123d import Axis, Box, Cylinder, Location

ROOT = Path(__file__).resolve().parents[1]
FEATURES_FILE = ROOT / "hw" / "out" / "board_features.json"
CASE_OUT = ROOT / "case" / "out"

import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from case import case  # noqa: E402


@pytest.fixture(scope="module")
def built_case():
    parts = case.build_and_export()
    feat = json.loads(FEATURES_FILE.read_text(encoding="utf8"))
    return parts, feat


def test_case_outputs_exist_and_nonempty(built_case):
    for fname in ("top_shell.step", "top_shell.stl", "bottom_shell.step", "bottom_shell.stl", "assembly.step"):
        p = CASE_OUT / fname
        assert p.is_file(), f"missing {p}"
        assert p.stat().st_size > 1000, f"empty {p}"


def test_shells_are_valid_watertight_solids_and_compact_pen_size(built_case):
    parts, _ = built_case
    for name in ("top_shell", "bottom_shell"):
        p = parts[name]
        assert p.is_valid, f"{name} invalid"
        assert len(p.solids()) == 1, f"{name} expected 1 solid, got {len(p.solids())}"
        assert p.volume > 1000.0

    top_bb = parts["top_shell"].bounding_box()
    bot_bb = parts["bottom_shell"].bounding_box()
    length = max(top_bb.max.X, bot_bb.max.X) - min(top_bb.min.X, bot_bb.min.X)
    width = max(top_bb.max.Y, bot_bb.max.Y) - min(top_bb.min.Y, bot_bb.min.Y)
    height = top_bb.max.Z - bot_bb.min.Z
    assert 133.0 <= length <= 145.0, length
    assert 17.0 <= width <= 23.0, width
    assert 9.0 <= height <= 16.0, height


def test_pcb_zero_collision_and_component_clearances(built_case):
    parts, feat = built_case
    pcb = parts["pcb"]
    top = parts["top_shell"]
    bot = parts["bottom_shell"]
    assert (top & pcb).volume < 1e-3, "top_shell collides with PCB"
    assert (bot & pcb).volume < 1e-3, "bottom_shell collides with PCB"

    shells = top + bot

    # WAGO wire entries: a 1.8 mm wire reaches each pole from in front of the nose
    for py in case.WAGO_POLE_Y:
        wire = Location((-3.0, py, case.WIRE_ENTRY_Z)) * Cylinder(radius=0.9, height=7.0).rotate(Axis.Y, 90)
        assert (shells & wire).volume < 1e-3, f"WAGO wire entry at y={py} obstructed"

    # WAGO release push-buttons reachable from above
    jx = feat["part_centres"]["J5"][0]
    for py in case.WAGO_POLE_Y:
        push = Location((jx + 3.0, py, 7.0)) * Box(4.0, 2.0, 4.0)
        assert (top & push).volume < 1e-3, f"WAGO push-button at y={py} covered"

    # Front work-light LEDs (B side, fire -X) see out of the nose
    for dx, dy in feat["leds"]:
        beam = Location((dx - 4.0, dy, -0.6)) * Cylinder(radius=0.6, height=8.0).rotate(Axis.Y, 90)
        assert (shells & beam).volume < 1e-3, f"LED at {(dx, dy)} blocked"

    # LCD on B.Cu: window through the bottom floor, glass not touching the shell
    lx0, ly0, lx1, ly1 = feat["lcd_window"]
    lcd_probe = Location((0.5 * (lx0 + lx1), 0.5 * (ly0 + ly1), -4.0)) * Box(lx1 - lx0 - 1.5, ly1 - ly0 - 1.5, 4.0)
    assert (bot & lcd_probe).volume < 1e-3, "LCD viewing window obstructed"
    gx0, gy0, gx1, gy1 = feat["lcd_glass"]
    glass = Location((0.5 * (gx0 + gx1), 0.5 * (gy0 + gy1), -1.0)) * Box(gx1 - gx0, gy1 - gy0, 1.6)
    assert (bot & glass).volume < 1e-3, "LCD glass collides with the bottom shell"
    rim = Location((0.5 * (gx0 + gx1), 0.0, -1.95)) * Box(gx1 - gx0 - 1.0, gy1 - gy0, 0.15)
    assert (bot & rim).volume > 1.0, "no ledge under the LCD glass rim"

    # Side buttons: plunger clear at rest, slots cut either side of each flexure tab
    for plunger in case.button_plungers(feat):
        assert (top & plunger).volume < 1e-3, "button plunger touches the case at rest"
    y_wall = -(feat["outline_mm"][1] / 2.0 + case.PCB_CLR_XY + case.WALL_THICK / 2.0)
    for bx, _ in feat["buttons"]:
        for sx in (-1, 1):
            slot = Location((bx + sx * (case.TAB_HALF_X + case.TAB_SLOT / 2.0), y_wall, 4.0)) * Box(0.3, 3.0, 3.0)
            assert (top & slot).volume < 1e-3, f"button {bx} flexure slot missing"
        tab = Location((bx, y_wall, 4.0)) * Box(3.0, 1.0, 2.0)
        assert (top & tab).volume > 1.0, f"button {bx} flexure tab missing"

    # Trimmer screwdriver holes
    for ref in ("CT1", "CT2"):
        tx, ty = feat["part_centres"][ref]
        bit = Location((tx, ty, -3.4)) * Cylinder(radius=0.9, height=3.0)
        assert (bot & bit).volume < 1e-3, f"{ref} trimmer hole missing"

    cons = {c["ref"]: c for c in feat["connectors"]}
    j3 = cons["J3"]["plug_envelope"]
    j3_probe = Location((0.5 * (j3[0] + j3[3]), 9.5, 0.5 * (j3[2] + j3[5]))) * Box(8.0, 4.0, 3.2)
    assert ((top + bot) & j3_probe).volume < 1e-3, "side J3 JST-SH opening obstructed"

    j1 = cons["J1"]["plug_envelope"]
    j1_probe = Location((134.0, 0.0, 0.5 * (j1[2] + j1[5]))) * Box(8.0, 8.8, 4.8)
    assert ((top + bot) & j1_probe).volume < 1e-3, "tail J1 USB-C opening obstructed"


def test_self_tapping_m2_pilot_holes_no_inserts(built_case):
    parts, feat = built_case
    assert 1.6 <= case.M2_PILOT_DIAM_MM <= 1.8
    bot = parts["bottom_shell"]
    top = parts["top_shell"]
    for hx, hy, _ in feat["holes"]:
        # 1.6 mm pin must clear the 1.7 mm pilot hole
        clear_pin = Location((hx, hy, -1.2)) * Cylinder(radius=0.80, height=2.0)
        assert (bot & clear_pin).volume < 1e-3, f"M2 pilot hole too small at {(hx, hy)}"
        # 2.4 mm cylinder must intersect boss wall (proves no >=3 mm heat-set insert pocket)
        wall_ring = Location((hx, hy, -1.2)) * Cylinder(radius=1.20, height=2.0)
        assert (bot & wall_ring).volume > 1.0, f"boss wall too thin / insert pocket present at {(hx, hy)}"
        # Top clearance 2.2 mm
        top_pin = Location((hx, hy, 4.5)) * Cylinder(radius=1.05, height=6.0)
        assert (top & top_pin).volume < 1e-3, f"top M2 clearance hole blocked at {(hx, hy)}"

    # The listed screw reaches 3+ mm into the pilot without poking through the floor
    tip_z = case.M2_CBORE_FLOOR_Z - case.M2_SCREW_LEN_MM
    assert tip_z <= -3.0, f"M2 x {case.M2_SCREW_LEN_MM} engages only {-tip_z:.1f} mm below the PCB"
    assert case.M2_PILOT_BOTTOM_Z <= tip_z - 0.2, "pilot shorter than the screw"
    floor = -(case.CAVITY_BOT + case.WALL_THICK)
    assert case.M2_PILOT_BOTTOM_Z - floor >= 0.4, "pilot breaks through the bottom"
    assert "M2 x %d mm" % case.M2_SCREW_LEN_MM in (ROOT / "hw" / "out" / "jlc" / "accessories.csv").read_text()
