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

    # Exterior access probes for every external connector, window, button, and LED
    nose_probe = Location((0.0, 0.0, case.Z_SPLIT)) * Cylinder(radius=1.8, height=12.0).rotate(Axis.Y, 90)
    assert ((top + bot) & nose_probe).volume < 1e-3, "nose J2 probe tip opening obstructed"

    lx0, ly0, lx1, ly1 = feat["lcd_window"]
    lcd_probe = Location((0.5 * (lx0 + lx1), 0.0, 6.5)) * Box(lx1 - lx0 - 1.5, ly1 - ly0 - 1.5, 4.0)
    assert (top & lcd_probe).volume < 1e-3, "LCD viewing window obstructed"

    for bx, by in feat["buttons"]:
        btn_probe = Location((bx, by, 6.5)) * Cylinder(radius=1.5, height=4.0)
        assert (top & btn_probe).volume < 1e-3, f"button cutout at {(bx, by)} obstructed"

    for dx, dy in feat["leds"]:
        led_probe = Location((dx, dy, 6.5)) * Cylinder(radius=0.8, height=4.0)
        assert (top & led_probe).volume < 1e-3, f"LED hole at {(dx, dy)} obstructed"

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
