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
    "MS9280": "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm",
    "RS2227XN": "Package_SO:MSOP-10_3x3mm_P0.5mm",
    "SN74LV4T125": "Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
    "RT9013-33GB": "Package_TO_SOT_SMD:SOT-23-5",
    "BAV199": "Package_TO_SOT_SMD:SOT-23",
}
CUSTOM_FOOTPRINTS = [
    "Molex_503480-0800_1x08-1MP_P0.50mm_Horizontal",
    "Wurth_9774030360R_M3",
    "C_Trimmer_Knowles_JZ100_4.5x3.2mm",
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
    path = (PRETTY / f"{name}.kicad_mod") if lib == "ppa" else (KICAD_SHARE / "footprints" / f"{lib}.pretty" / f"{name}.kicad_mod")
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

    ms = pin_map("MS9280")
    assert ms["AVSS"] == "1" and ms["DRVDD"] == "2" and ms["OTR"] == "13" and ms["DRVSS"] == "14"
    assert ms["CLK"] == "15" and ms["STBY"] == "17" and ms["REFSENSE"] == "18" and ms["MODE"] == "23"
    assert ms["VREF"] == "26" and ms["AIN"] == "27" and ms["AVDD"] == "28"
    for i in range(10):
        assert ms[f"D{i}"] == str(3 + i)

    rs = pin_map("RS2227XN")
    assert rs == {
        "V+": "1", "S": "2", "D+": "3", "D-": "4", "GND": "5",
        "HSD1-": "6", "HSD1+": "7", "HSD2-": "8", "HSD2+": "9", "~{OE}": "10",
    }

    lv = pin_map("SN74LV4T125")
    assert lv == {
        "1~{OE}": "1", "1A": "2", "1Y": "3", "2~{OE}": "4", "2A": "5", "2Y": "6", "GND": "7",
        "3Y": "8", "3A": "9", "3~{OE}": "10", "4Y": "11", "4A": "12", "4~{OE}": "13", "VCC": "14",
    }

    rt = pin_map("RT9013-33GB")
    assert rt == {"VIN": "1", "GND": "2", "EN": "3", "NC": "4", "VOUT": "5"}

    bav = pin_map("BAV199")
    assert bav == {"A1": "1", "K2": "2", "K1_A2": "3"}
