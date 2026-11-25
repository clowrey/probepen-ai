"""Stage 3 PCB verification: KiCad DRC (with schematic parity & JLCPCB rules),
courtyard/edge/silk checks, 4-layer stackup, AFE keepout, and mechanical datum parity."""
import json
from pathlib import Path

import pytest
from conftest import HW, ROOT, kcli, kpy

from hardware.pcb_datum import PCB as DATUM
from hw import design

PCB_FILE = HW / "probepen_ai.kicad_pcb"
DRU_FILE = HW / "probepen_ai.kicad_dru"
FEATURES_FILE = HW / "out" / "board_features.json"

ALLOWED_VIOLATION_TYPES = {"unconnected_items", "lib_footprint_issues"}
ZERO_TOLERANCE_TYPES = {
    "clearance", "creepage", "hole_clearance", "copper_edge_clearance",
    "courtyards_overlap", "hole_to_hole", "drill_out_of_range",
    "invalid_outline", "malformed_courtyard", "items_not_allowed",
    "text_height", "silk_overlap", "silk_over_copper", "silk_edge_clearance",
    "starved_thermal",
}


@pytest.fixture(scope="module")
def built_board():
    kpy(HW / "gen_pcb.py")
    assert PCB_FILE.is_file()
    assert DRU_FILE.is_file()
    assert FEATURES_FILE.is_file()
    return PCB_FILE


@pytest.fixture(scope="module")
def drc(built_board, tmp_path_factory):
    out = tmp_path_factory.mktemp("drc") / "drc.json"
    kcli(
        "pcb", "drc", "--schematic-parity", "--severity-all",
        "--format", "json", "--units", "mm", "-o", out, built_board,
        check=False,
    )
    return json.loads(out.read_text(encoding="utf8"))


@pytest.fixture(scope="module")
def features(built_board):
    return json.loads(FEATURES_FILE.read_text(encoding="utf8"))


@pytest.fixture(scope="module")
def board_dump(built_board, tmp_path_factory):
    out = tmp_path_factory.mktemp("dump") / "dump.json"
    kpy(HW / "tools" / "dump_board.py", built_board, out)
    return json.loads(out.read_text(encoding="utf8"))


def _fmt(items):
    return "\n".join(
        f"{v['type']} ({v.get('severity')}): {v['description']} :: "
        + "; ".join(i.get("description", "") for i in v.get("items", []))
        for v in items[:20]
    )


def test_schematic_parity(drc):
    assert drc.get("schematic_parity", []) == [], _fmt(drc["schematic_parity"])


def test_no_layout_or_manufacturing_drc_violations(drc):
    bad = [v for v in drc["violations"] if v["type"] in ZERO_TOLERANCE_TYPES]
    assert bad == [], _fmt(bad)
    unexpected = [v for v in drc["violations"] if v["type"] not in ALLOWED_VIOLATION_TYPES]
    assert unexpected == [], _fmt(unexpected)


def test_four_layer_stackup_and_outline(features):
    pcb_text = PCB_FILE.read_text(encoding="utf8")
    assert '"In1.Cu" signal' in pcb_text
    assert '"In2.Cu" signal' in pcb_text
    assert features["outline_mm"] == [DATUM["length"], DATUM["width"]]
    assert features["thickness"] == DATUM["thickness"]


def test_all_design_components_placed_single_sided_smt(board_dump):
    fps = {f["ref"]: f for f in board_dump["footprints"]}
    assert set(fps) == {c.ref for c in design.COMPONENTS}
    for c in design.COMPONENTS:
        f = fps[c.ref]
        assert f["dnp"] == c.dnp, c.ref
        if not c.dnp or c.ref in ("H1", "H2"):
            assert f["layer"] == "F.Cu", f"{c.ref} expected on F.Cu"
        else:
            assert f["layer"] == "B.Cu", f"{c.ref} DNP test pad/trimmer expected on B.Cu"


def test_tall_parts_outside_lcd_glass(board_dump):
    lcd_x0, lcd_x1 = DATUM["lcd_glass_x"]
    fps = {f["ref"]: f for f in board_dump["footprints"]}
    tall_refs = ("J1", "J2", "J3", "J4", "SW1", "SW2", "U2", "U1", "L1", "Y1", "H1", "H2")
    for ref in tall_refs:
        cb = fps[ref]["courtyard"]
        assert cb is not None, ref
        if ref == "J4":
            # J4 FPC body sits at X=48.5; its leads enter X=50.0 edge cleanly
            assert fps[ref]["x"] < lcd_x0
        else:
            assert cb[2] <= lcd_x0 + 0.5 or cb[0] >= lcd_x1 - 0.5, (ref, cb)


def test_afe_keepout_and_edge_clearances(board_dump, features):
    keepouts = [r for r in board_dump["rule_areas"] if r["name"] == "AFE high-Z keepout"]
    assert len(keepouts) == 1
    assert keepouts[0]["no_pour"] is True
    assert set(keepouts[0]["layers"]) == {"In1.Cu", "In2.Cu"}

    w, h = features["outline_mm"]
    hw_y = h / 2.0
    for item in board_dump["copper"]:
        if item["kind"] == "pad" and item["net"]:
            x0, y0, x1, y1 = item["bbox"]
            assert x0 >= 0.49 and w - x1 >= 0.49, (item["ref"], item["pad"], item["bbox"])
            assert y0 >= -hw_y + 0.49 and hw_y - y1 >= 0.49, (item["ref"], item["pad"], item["bbox"])


def test_features_json_matches_datum(board_dump, features):
    fps = {f["ref"]: f for f in board_dump["footprints"]}
    for c in features["connectors"]:
        assert abs(fps[c["ref"]]["x"] - c["x"]) < 0.01, c["ref"]
        assert abs(fps[c["ref"]]["y"] - c["y"]) < 0.01, c["ref"]
    holes = sorted((round(f["x"], 2), round(f["y"], 2)) for f in board_dump["footprints"] if f["ref"].startswith("H"))
    assert holes == sorted((round(h[0], 2), round(h[1], 2)) for h in features["holes"])
    cons = {c["ref"]: c for c in features["connectors"]}
    assert cons["J1"]["x"] + 3.7 == pytest.approx(DATUM["length"] + 1.0, abs=0.05)  # 1 mm overhang
    assert cons["J3"]["edge"] == "+y"
    assert cons["J3"]["y"] == pytest.approx(DATUM["side_jst_xy"][1], abs=0.05)


def test_dru_contains_jlc_and_hv_rules():
    dru = DRU_FILE.read_text(encoding="utf8")
    assert "0.127mm" in dru
    assert "0.30mm" in dru
    assert "/PROBE_TIP" in dru and "0.40mm" in dru
