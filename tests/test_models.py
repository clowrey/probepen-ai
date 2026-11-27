"""Every part on the board has a 3D model that resolves (bare test pads and mounting holes excepted)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "hw" / "probepen_ai.kicad_pcb"
NO_BODY = ("MountingHole", "TestPoint")


def footprints():
    text = BOARD.read_text(encoding="utf8")
    for block in text.split("\n\t(footprint ")[1:]:
        name = block.split('"', 2)[1]
        ref = re.search(r'\(property "Reference" "([^"]+)"', block).group(1)
        models = re.findall(r'\(model "([^"]+)"', block)
        yield ref, name, models


def test_every_part_has_a_model_file():
    missing = []
    for ref, name, models in footprints():
        if any(k in name for k in NO_BODY):
            continue
        paths = [Path(m.replace("${KIPRJMOD}", str(BOARD.parent))) for m in models]
        if not paths or not all(p.is_file() for p in paths):
            missing.append(f"{ref} ({name}): {models}")
    assert not missing, "parts without a 3D model:\n" + "\n".join(missing)


def test_generated_models_carry_colours():
    gen = sorted((ROOT / "hw" / "lib" / "ppa.3dshapes" / "gen").glob("*.step"))
    assert len(gen) >= 20
    for p in gen:
        assert "COLOUR_RGB" in p.read_text(encoding="utf8", errors="replace"), p.name
