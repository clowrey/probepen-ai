"""JLCPCB fabrication & PCBA exports: hw/out/jlc/{gerbers.zip, bom.csv, cpl.csv, accessories.csv, cost.md} + hw/out/board.step.

Run with local KiCad Python:
    ~/.local/bin/kicad-python hw/export_jlc.py [--offline]
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hw import design  # noqa: E402
from hw.gen_pcb import HW_Y, L, OX, OY, PCB_FILE, W  # noqa: E402
from hw.parts import PARTS  # noqa: E402

OUT_ROOT = HERE / "out"
OUT = OUT_ROOT / "jlc"
STEP_OUT = OUT_ROOT / "board.step"
KCLI = shutil.which("kicad-cli") or str(Path.home() / ".local" / "bin" / "kicad-cli")
QTYS = (10, 20, 30, 50, 100)

EXTENDED_FEE = 3.00
PCBA_SETUP = 8.24 + 0.99
STENCIL = 1.55
SMT_JOINT = 0.0017
THT_JOINT = 0.0173
PCB_FAB_4L = {10: 14.0, 20: 22.0, 30: 30.0, 50: 45.0, 100: 78.0}
SHIPPING = {10: 20.0, 20: 25.0, 30: 30.0, 50: 40.0, 100: 60.0}


def placed():
    return [c for c in design.COMPONENTS if c.part and PARTS[c.part].footprint and not c.dnp]


def gerbers_and_step() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        layers = "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"
        subprocess.run(
            [
                str(KCLI), "pcb", "export", "gerbers",
                "--layers", layers, "--use-drill-file-origin", "--no-protel-ext",
                "-o", tmp + "/", str(PCB_FILE),
            ],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            [
                str(KCLI), "pcb", "export", "drill",
                "--drill-origin", "plot", "--excellon-separate-th",
                "--generate-map", "--map-format", "gerberx2",
                "-o", tmp + "/", str(PCB_FILE),
            ],
            check=True, capture_output=True, text=True,
        )
        with zipfile.ZipFile(OUT / "gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(Path(tmp).iterdir()):
                z.write(f, f.name)

    cmd = [
        str(KCLI), "pcb", "export", "step",
        "--subst-models", "--drill-origin",
        "--output", str(STEP_OUT), str(PCB_FILE),
    ]
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if res.returncode != 0 or not STEP_OUT.is_file() or STEP_OUT.stat().st_size == 0:
        subprocess.run(
            [str(KCLI), "pcb", "export", "step", "--drill-origin", "--output", str(STEP_OUT), str(PCB_FILE)],
            check=True, capture_output=True, text=True,
        )


def bom():
    groups = defaultdict(list)
    for c in placed():
        groups[c.part].append(c.ref)
    with open(OUT / "bom.csv", "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "JLCPCB Part #"])
        for key, refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
            p = PARTS[key]
            fp_name = p.footprint.split(":", 1)[1] if ":" in p.footprint else p.footprint
            w.writerow([p.mpn or p.value, ",".join(refs), fp_name, p.lcsc])
    return groups


def cpl() -> None:
    board = pcbnew.LoadBoard(str(PCB_FILE.resolve()))
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    with open(OUT / "cpl.csv", "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer"])
        for c in placed():
            fp = fps[c.ref]
            xs = [b for p in fp.Pads() for b in (p.GetBoundingBox().GetLeft(), p.GetBoundingBox().GetRight())]
            ys = [b for p in fp.Pads() for b in (p.GetBoundingBox().GetTop(), p.GetBoundingBox().GetBottom())]
            if xs and ys:
                x = (min(xs) + max(xs)) / 2e6 - OX
                y = (OY + HW_Y) - (min(ys) + max(ys)) / 2e6
            else:
                pos = fp.GetPosition()
                x = pcbnew.ToMM(pos.x) - OX
                y = (OY + HW_Y) - pcbnew.ToMM(pos.y)
            p_info = PARTS[c.part]
            offset_rot = getattr(p_info, "jlc_rot", getattr(p_info, "rotation", 0))
            rot = (fp.GetOrientationDegrees() + offset_rot) % 360
            layer = "Bottom" if fp.GetLayer() == pcbnew.B_Cu else "Top"
            fp_name = p_info.footprint.split(":", 1)[1] if ":" in p_info.footprint else p_info.footprint
            w.writerow([c.ref, c.value, fp_name, f"{x:.3f}mm", f"{y:.3f}mm", f"{rot:g}", layer])


def accessories() -> None:
    with open(OUT / "accessories.csv", "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["Item", "Spec", "Qty per board", "Notes"])
        w.writerow(["IPS Display", "FP-096H09A 0.96in 80x160 ST7735S 8P FPC", 1, "Mates to J4 Molex 503480-0800"])
        w.writerow(["Probe Tip", "M3 stainless steel pogo/needle tip", 1, "Threads into J2 SMD M3 boss"])
        w.writerow(["Logic Pod Cable", "JST-SH 6P SHR-06V-S-B + micro-grabber harness", 1, "Plugs into J3"])
        w.writerow(["Case Fasteners", "M2 x 6 mm self-tapping pan head screw", 2, "Self-taps into 1.7 mm case bosses"])


def joints() -> tuple[int, int]:
    smt = tht = 0
    board = pcbnew.LoadBoard(str(PCB_FILE.resolve()))
    refs = {c.ref for c in placed()}
    for fp in board.GetFootprints():
        if fp.GetReference() not in refs:
            continue
        for p in fp.Pads():
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
                tht += 1
            elif p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
                smt += 1
    return smt, tht


def cost(groups: dict[str, list[str]], offline: bool = False) -> None:
    from hw.tools import jlc
    info = {}
    for key in groups:
        p = PARTS[key]
        try:
            info[key] = None if offline else jlc.lookup(p.lcsc)
        except Exception as exc:
            info[key] = None
            print("price lookup skipped", key, exc)

    per_board = {k: len(v) for k, v in groups.items()}
    extended = sorted(
        k for k in groups
        if (info.get(k) and info[k]["lib"] != "base") or (not info.get(k) and not PARTS[k].basic)
    )
    smt, tht = joints()

    lines = [
        f"# ProbePen-AI: cost estimate ({date.today().isoformat()})\n",
        "Parts prices are live from JLC when online; fees, 4-layer PCB fabrication, and shipping are estimates.\n",
        f"- Board: {L:.0f} x {W:.0f} mm, 4-layer JLC04161H-7628",
        f"- Unique placed parts: {len(groups)}; Extended parts: {len(extended)} (${EXTENDED_FEE:.2f} each per order)",
        f"- Joints per board: {smt} SMT, {tht} through-hole\n",
        "| Boards | Parts | Extended fees | Setup + stencil | Joints | PCB 4L (est.) | Shipping (est.) | Total | Per board |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n in QTYS:
        parts_cost = 0.0
        for key, qty in per_board.items():
            if info.get(key) and info[key]["prices"]:
                parts_cost += jlc.price_at(info[key]["prices"], qty * n) * qty * n
        fees = EXTENDED_FEE * len(extended)
        setup = PCBA_SETUP + STENCIL
        jnt = (smt * SMT_JOINT + tht * THT_JOINT) * n
        total = parts_cost + fees + setup + jnt + PCB_FAB_4L[n] + SHIPPING[n]
        lines.append(
            f"| {n} | ${parts_cost:.2f} | ${fees:.2f} | ${setup:.2f} | ${jnt:.2f} "
            f"| ${PCB_FAB_4L[n]:.2f} | ${SHIPPING[n]:.2f} | ${total:.2f} | ${total / n:.2f} |"
        )

    lines.extend([
        "\n## Parts\n",
        "| Part | LCSC | Per board | Type | Stock | Unit @30 boards |",
        "|---|---|---|---|---|---|",
    ])
    for key, qty in sorted(per_board.items()):
        p = PARTS[key]
        i = info.get(key)
        if i and i["prices"]:
            lib = "Basic" if i["lib"] == "base" else "Extended"
            lines.append(
                f"| {p.mpn or p.value} | {p.lcsc} | {qty} | {lib} | {i['stock']} | ${jlc.price_at(i['prices'], qty * 30):.4f} |"
            )
        else:
            lib = "Basic" if p.basic else "Extended"
            lines.append(f"| {p.mpn or p.value} | {p.lcsc} | {qty} | {lib} | ? | (offline) |")
    (OUT / "cost.md").write_text("\n".join(lines) + "\n", encoding="utf8", newline="\n")


def main() -> None:
    offline = "--offline" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    gerbers_and_step()
    groups = bom()
    cpl()
    accessories()
    cost(groups, offline=offline)
    print("wrote", sorted(p.name for p in OUT.iterdir()), "and", STEP_OUT.name)


if __name__ == "__main__":
    main()
