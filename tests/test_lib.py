"""Custom symbols and footprints in hw/lib parse in KiCad 10 and match datasheet pin/pad maps."""
import re
import shutil

import pytest
from conftest import HW, KICAD_SHARE, kcli

from hw import design

LIB = HW / "lib"
SYM = LIB / "ppa.kicad_sym"
PRETTY = LIB / "ppa.pretty"

CUSTOM_SYMBOLS = {  # symbol -> footprint (ppa: or standard KiCad)
    "AD9288": "Package_QFP:LQFP-48_7x7mm_P0.5mm",
    "RS8752": "Package_SO:MSOP-8_3x3mm_P0.65mm",
    "74LVC2G66": "Package_SO:TSSOP-8_3x3mm_P0.65mm",
    "RT9013-33GB": "Package_TO_SOT_SMD:SOT-23-5",
    "BAV199": "Package_TO_SOT_SMD:SOT-23",
    "SW_Side_TS24CA": "ppa:SW_Push_Side_TS24CA",
}
CUSTOM_FOOTPRINTS = [
    "LCD_FPC_13P_P0.70mm_Solder",
    "SW_Push_Side_TS24CA",
    "Nose_WAGO2060_KF381_ProbePads",
    "C_Trimmer_Knowles_JZ100_4.5x3.2mm",
    "LED_Side_XL3806_3.8x1.0mm",
]


def _sym_block(name: str) -> str:
    text = SYM.read_text(encoding="utf8")
    start = text.index(f'(symbol "{name}"')
    nxt = text.find('\n\t(symbol "', start + 10)
    return text[start:nxt if nxt > 0 else len(text)]


def _sym_pins(name: str) -> set[str]:
    return set(re.findall(r'\(number "([^"]+)"', _sym_block(name)))


def _fp_pads(fp: str) -> set[str]:
    lib, name = fp.split(":")
    path = ((PRETTY / f"{name}.kicad_mod") if lib == "ppa" else (LIB / "rpi.pretty" / f"{name}.kicad_mod") if lib == "rpi"
            else (KICAD_SHARE / "footprints" / f"{lib}.pretty" / f"{name}.kicad_mod"))
    text = path.read_text(encoding="utf8")
    return {p for p in re.findall(r'\(pad "([^"]*)"', text) if p}


def test_symbols_parse(tmp_path):
    dst = tmp_path / "ppa.kicad_sym"
    shutil.copy(SYM, dst)
    kcli("sym", "upgrade", "--force", dst)


def test_footprints_parse(tmp_path):
    dst = tmp_path / "ppa.pretty"
    shutil.copytree(PRETTY, dst)
    kcli("fp", "upgrade", "--force", dst)
    for name in CUSTOM_FOOTPRINTS:
        assert (dst / f"{name}.kicad_mod").is_file()


@pytest.mark.parametrize("sym", CUSTOM_SYMBOLS)
def test_custom_symbol_pin_pad_match(sym):
    assert _sym_pins(sym) == _fp_pads(CUSTOM_SYMBOLS[sym])


def test_custom_footprint_pads_cover_design():
    for c in design.COMPONENTS:
        if c.footprint.startswith("ppa:"):
            pads = _fp_pads(c.footprint)
            missing = set(c.pins) - pads
            assert not missing, f"{c.ref} ({c.footprint}) pins missing from footprint pads: {sorted(missing)}"


def test_custom_symbol_pinouts_match_datasheets():
    def pin_map(name):
        blk = _sym_block(name)
        return dict(re.findall(r'\(name "([^"]+)"[^\n]*\n\s*\(number "(\d+)"', blk))

    ad = pin_map("AD9288")
    assert ad["AINA"] == "2" and ad["~{AINA}"] == "3" and ad["DFS"] == "4"
    assert ad["REFINA"] == "5" and ad["REFOUT"] == "6" and ad["REFINB"] == "7"
    assert ad["S1"] == "8" and ad["S2"] == "9"
    assert ad["~{AINB}"] == "10" and ad["AINB"] == "11"
    assert ad["ENCB"] == "14" and ad["ENCA"] == "47"
    for i in range(8):
        assert ad[f"D{i}A"] == str(37 + i)
        assert ad[f"D{i}B"] == str(24 - i)

    rs87 = pin_map("RS8752")
    assert rs87 == {
        "OUTA": "1", "-INA": "2", "+INA": "3", "V-": "4",
        "+INB": "5", "-INB": "6", "OUTB": "7", "V+": "8",
    }

    sw = pin_map("74LVC2G66")
    assert sw == {
        "1Y": "1", "1Z": "2", "2E": "3", "GND": "4",
        "2Y": "5", "2Z": "6", "1E": "7", "VCC": "8",
    }

    rt = pin_map("RT9013-33GB")
    assert rt == {"VIN": "1", "GND": "2", "EN": "3", "NC": "4", "VOUT": "5"}

    bav = pin_map("BAV199")
    assert bav == {"A1": "1", "K2": "2", "K1_A2": "3"}
