"""JLCPCB fabrication & assembly export checks for ProbePen-AI."""
import csv
import json
import re
import zipfile

import pytest
from conftest import HW, kpy

from hw import design
from hw.parts import PARTS

OUT_ROOT = HW / "out"
JLC = OUT_ROOT / "jlc"
PLACED = [c for c in design.COMPONENTS if c.part and PARTS[c.part].footprint and not c.dnp]


@pytest.fixture(scope="module")
def exported_jlc():
    if not (HW / "probepen_ai.kicad_pcb").is_file():
        kpy(HW / "gen_pcb.py")
    kpy(HW / "export_jlc.py", "--offline")
    return JLC


def _rows(name):
    with open(JLC / name, newline="", encoding="utf8") as f:
        return list(csv.DictReader(f))


def test_bom_designators_match_pcb(exported_jlc):
    rows = _rows("bom.csv")
    got = sorted(d.strip() for r in rows for d in r["Designator"].split(","))
    assert got == sorted(c.ref for c in PLACED)
    for r in rows:
        assert re.fullmatch(r"C\d+", r["JLCPCB Part #"]), r
        refs = [d.strip() for d in r["Designator"].split(",")]
        assert {design.comp(x).lcsc for x in refs} == {r["JLCPCB Part #"]}


def test_cpl_bounds_and_parity(exported_jlc):
    rows = _rows("cpl.csv")
    feat = json.loads((OUT_ROOT / "board_features.json").read_text(encoding="utf8"))
    w, h = feat["outline_mm"]
    hw_y = h / 2.0
    centres = feat["part_centres"]

    assert sorted(r["Designator"] for r in rows) == sorted(c.ref for c in PLACED)
    for r in rows:
        assert r["Layer"] in ("Top", "Bottom")
        x = float(r["Mid X"].removesuffix("mm"))
        y = float(r["Mid Y"].removesuffix("mm"))
        rot = float(r["Rotation"])
        assert 0.0 <= x <= w and 0.0 <= y <= h, r
        assert 0.0 <= rot < 360.0, r
        if r["Designator"] in centres:
            cx, cy_cad = centres[r["Designator"]]
            assert abs(x - cx) < 0.05 and abs(y - (hw_y + cy_cad)) < 0.05, (r["Designator"], x, y, cx, cy_cad)


def test_gerber_zip_all_4_layers_and_drills(exported_jlc):
    with zipfile.ZipFile(JLC / "gerbers.zip") as z:
        names = " ".join(z.namelist()).lower()
    for part in ("f_cu", "in1_cu", "in2_cu", "b_cu", "f_mask", "b_mask", "f_silkscreen", "b_silkscreen", "f_paste", "edge_cuts"):
        assert part in names, part
    assert "pth" in names and "npth" in names


def test_board_step_and_cost_sheet(exported_jlc):
    step = OUT_ROOT / "board.step"
    assert step.is_file() and step.stat().st_size > 1000
    text = (JLC / "cost.md").read_text(encoding="utf8")
    for q in (10, 20, 30, 50, 100):
        assert re.search(rf"^\|\s*{q}\s*\|", text, re.M), q
    acc = _rows("accessories.csv")
    assert len(acc) >= 3


def test_jlc_rotation_offsets_checked_in_the_viewer():
    """Offsets confirmed in JLC's placement viewer on 2026-11-27 (pin-1 dot on the footprint's pad 1)."""
    from hw.parts import PARTS
    want = {"rs8752": 270, "lvc2g66": 180, "ref3030": 180, "rt9013": 270, "srv05_4": 270, "bav199": 180,
            "ao3401a": 180, "rp2354b": 0, "xtal_12m": 0, "1n5819ws": 0}
    got = {k: PARTS[k].rotation for k in want if k in PARTS}
    assert got == {k: v for k, v in want.items() if k in PARTS}
