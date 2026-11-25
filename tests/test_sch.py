"""The generated schematic: KiCad ERC clean and exported netlist equals design.NETS."""
import json
import re

import pytest
from conftest import HW, kcli

from hw import design

SCH = HW / "probepen_ai.kicad_sch"


@pytest.fixture(scope="module")
def netlist(tmp_path_factory):
    out = tmp_path_factory.mktemp("net") / "net.net"
    kcli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, SCH)
    return out.read_text(encoding="utf8")


def _parse_nets(text):
    nets = {}
    for m in re.finditer(
        r'\(net\s+\(code "?\d+"?\)\s+\(name "([^"]*)"\)(.*?)\n\s*\)\s*(?=\(net|\)\s*\)\s*$|\)\s*$)',
        text, re.S,
    ):
        name = m.group(1).lstrip("/")
        nodes = re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', m.group(2))
        nets[name] = sorted(n for n in nodes if not n[0].startswith("#"))
    return {k: v for k, v in nets.items() if v and not k.startswith("unconnected-")}


def test_erc_clean(tmp_path):
    out = tmp_path / "erc.json"
    r = kcli(
        "sch", "erc", "--format", "json", "--severity-error", "--exit-code-violations",
        "-o", out, SCH, check=False,
    )
    data = json.loads(out.read_text(encoding="utf8"))
    errors = [v for s in data["sheets"] for v in s["violations"]]
    assert errors == [] and r.returncode == 0, json.dumps(errors[:10], indent=1)


def test_netlist_matches_design(netlist):
    got = _parse_nets(netlist)
    want = {n: sorted(nodes) for n, nodes in design.NETS.items()}
    assert set(got) == set(want), set(got) ^ set(want)
    for n in want:
        assert got[n] == want[n], n


def test_lcsc_field_on_every_symbol():
    text = SCH.read_text(encoding="utf8")
    for c in design.COMPONENTS:
        if c.part:
            assert re.search(rf'\(property "Reference" "{c.ref}".*?\(property "LCSC" "{c.lcsc}"', text, re.S), c.ref


def test_footprint_field_matches_design():
    text = SCH.read_text(encoding="utf8")
    for c in design.COMPONENTS:
        m = re.search(rf'\(property "Reference" "{c.ref}"[\s\S]*?\(property "Footprint" "([^"]*)"', text)
        assert m and m.group(1) == c.footprint, c.ref
