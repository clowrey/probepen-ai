"""Part catalogue checks: LCSC format, Basic/Extended flags, KiCad 10 symbols/footprints, and live JLC stock."""
import re
from functools import lru_cache

import pytest
from conftest import HW, KICAD_SHARE

from hw import design
from hw.parts import PARTS


def _fp_file(fp: str):
    lib, name = fp.split(":")
    if lib == "ppa":
        return HW / "lib" / "ppa.pretty" / f"{name}.kicad_mod"
    return KICAD_SHARE / "footprints" / f"{lib}.pretty" / f"{name}.kicad_mod"


def _extract_block(txt: str, start: int) -> str:
    depth = 0
    for i in range(start, len(txt)):
        if txt[i] == "(":
            depth += 1
        elif txt[i] == ")":
            depth -= 1
            if depth == 0:
                return txt[start:i + 1]
    raise ValueError("unbalanced S-expression")


@lru_cache(maxsize=None)
def _symbol_pin_numbers(sym_id: str) -> set[str]:
    lib, name = sym_id.split(":")
    path = (HW / "lib" / "ppa.kicad_sym") if lib == "ppa" else (KICAD_SHARE / "symbols" / f"{lib}.kicad_sym")
    txt = path.read_text(encoding="utf-8")
    m = re.search(rf'\(symbol "{re.escape(name)}"\s', txt)
    assert m, f"Missing KiCad symbol {sym_id}"
    blk = _extract_block(txt, m.start())
    ext = re.search(r'\(extends "([^"]+)"\)', blk)
    if ext:
        return _symbol_pin_numbers(f"{lib}:{ext.group(1)}")
    pins = set()
    for pm in re.finditer(r"\(pin\s+\w+\s+\w+", blk):
        pb = _extract_block(blk, pm.start())
        num = re.search(r'\(number "([^"]*)"', pb)
        if num:
            pins.add(num.group(1))
    return pins


def test_every_placed_part_has_lcsc():
    for k, p in PARTS.items():
        if p.footprint:
            assert p.lcsc.startswith("C") and p.lcsc[1:].isdigit(), k


@pytest.mark.parametrize("key", [k for k, p in PARTS.items() if p.footprint])
def test_footprints_exist(key):
    assert _fp_file(PARTS[key].footprint).is_file(), PARTS[key].footprint


@pytest.mark.parametrize("key", [k for k, p in PARTS.items() if p.symbol])
def test_symbols_exist(key):
    pins = _symbol_pin_numbers(PARTS[key].symbol)
    assert pins, f"No pins parsed for {PARTS[key].symbol}"


def test_all_component_pin_numbers_match_kicad_symbols():
    for c in design.COMPONENTS:
        if not c.symbol:
            continue
        sym_pins = _symbol_pin_numbers(c.symbol)
        missing = set(c.pins) - sym_pins
        assert not missing, f"{c.ref} ({c.symbol}) pins not in KiCad symbol: {sorted(missing)}"


@pytest.mark.net
@pytest.mark.parametrize("key", list(PARTS))
def test_all_parts_in_stock_for_prototype(key):
    from hw.tools import jlc

    p = PARTS[key]
    info = jlc.lookup(p.lcsc)
    need = 5 * p.per_board
    assert info["stock"] >= need, f"{key} ({p.lcsc}): stock {info['stock']} < {need}"
    assert p.basic == (info["lib"] == "base"), (
        f"{key} ({p.lcsc}): basic={p.basic} but JLC lib={info['lib']}"
    )
    if p.extra.get("preferred"):
        assert info["preferred"], (
            f"{key} ({p.lcsc}): marked preferred in parts.py but JLCPCB preferred={info['preferred']}"
        )
