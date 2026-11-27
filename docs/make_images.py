"""README images: docs/img/*.png and docs/img/block-diagram.svg.

    .venv/bin/python docs/make_images.py [sch] [layers] [render] [case]

Runs with the local project venv (pymupdf, Pillow, matplotlib, build123d) and local
WSL kicad-cli:
  sch     schematic sections, cropped from schematic PDF
  layers  copper, inner planes, mask and silkscreen on dark background
  render  3D views of the board (kicad-cli pcb render, raytraced)
  case    3D views of the enclosure and PCB assembly
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
KCLI = shutil.which("kicad-cli") or str(Path.home() / ".local" / "bin" / "kicad-cli")
PCB = ROOT / "hw" / "probepen_ai.kicad_pcb"
SCH = ROOT / "hw" / "probepen_ai.kicad_sch"
BG = (16, 20, 28)


def kcli(*args) -> None:
    subprocess.run([str(KCLI), *map(str, args)], check=True, capture_output=True)


SECTIONS = {   # the block titles hw/gen_sch.py writes on the schematic
    "USB-C, 500 mA PTC, CC pulldowns, SRV05-4 ESD": "sch_usb",
    "Power Tree: +3V3_DIG, +3V3_ANA, +3V0_REF, Star GND, Test Pads": "sch_power",
    "RP2354B QFN-80, 1.1 V Buck, 12 MHz XTAL, BOOTSEL, RUN, SWD": "sch_mcu",
    "Dual-Channel 2-Gain Single-Supply AFE: 1M Dividers, BAV199, Dual RS8752XM, 74LVC2G66DP": "sch_afe",
    "Dual High-Speed ADC: AD9288BSTZ-80 LQFP-48 (2x 8-bit 80 MSPS, Single-Ended 1.024 Vpp)": "sch_adc",
    "6-Pin Bidirectional GPIO Expansion Header (JST-SH, 500 mA PTC, 220R, SRV05-4 ESD)": "sch_logic",
    "1.14in/0.96in IPS LCD (13P 0.7mm Solder FPC + AO3401A Backlight PWM)": "sch_lcd",
    "User Interface: 4x Side Buttons (AI, MODE/BOOTSEL, UP, DOWN) + 2x XL-3806UWC Front Worklight LEDs": "sch_ui",
}


def schematic() -> None:
    import pymupdf
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "sch.pdf"
        kcli("sch", "export", "pdf", "--exclude-drawing-sheet", "-o", pdf, SCH)
        doc = pymupdf.open(pdf)
        page = doc[0]
        titles = {}
        for title, name in SECTIONS.items():
            hits = page.search_for(title)
            assert hits, f"section title not found: {title}"
            titles[name] = hits[0]

        items = [d["rect"] for d in page.get_drawings()]
        items += [pymupdf.Rect(b[:4]) for b in page.get_text("blocks")]
        xs = sorted({round(r.x0) for r in titles.values()})
        out = {}
        whole = None
        for name, t in titles.items():
            col_right = min([x for x in xs if x > t.x0 + 5] + [page.rect.x1])
            below = [r.y0 for r in titles.values() if abs(r.x0 - t.x0) < 5 and r.y0 > t.y0 + 5]
            region = pymupdf.Rect(t.x0 - 15, t.y0 - 5, col_right - 15, min(below + [page.rect.y1]) - 5)
            box = pymupdf.Rect(t)
            for r in items:
                if r.is_empty or r.width > 450 or r.height > 450:
                    continue
                if region.contains(r):
                    box |= r
            box = box + (-8, -8, 8, 8)
            whole = pymupdf.Rect(box) if whole is None else whole | box
            pix = page.get_pixmap(dpi=200, clip=box)
            pix.save(IMG / f"{name}.png")
            out[name] = (pix.width, pix.height)

        pix = page.get_pixmap(dpi=115, clip=whole + (-10, -10, 10, 10))
        pix.save(IMG / "schematic.png")
        out["schematic"] = (pix.width, pix.height)
        doc.close()
    for k, v in out.items():
        print(k, v)


LAYERS = {
    "layer_top": "B.Cu,In2.Cu,F.Cu,F.Silkscreen,Edge.Cuts",
    "layer_all": "B.Cu,In2.Cu,In1.Cu,F.Cu,Edge.Cuts",
    "layer_f_cu": "F.Cu,Edge.Cuts",
    "layer_in1_cu": "In1.Cu,Edge.Cuts",
    "layer_in2_cu": "In2.Cu,Edge.Cuts",
    "layer_b_cu": "B.Cu,Edge.Cuts",
    "layer_f_silk": "F.Silkscreen,Edge.Cuts",
    "layer_f_mask": "F.Mask,Edge.Cuts",
}


def layers(width: int = 1800) -> None:
    import pymupdf
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        for name, lay in LAYERS.items():
            svg = Path(tmp) / f"{name}.svg"
            kcli("pcb", "export", "svg", "--mode-single", "--layers", lay, "--page-size-mode", "2",
                 "--exclude-drawing-sheet", "--drill-shape-opt", "2", "-o", svg, PCB)
            doc = pymupdf.open(svg)
            page = doc[0]
            zoom = width / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
            doc.close()
            fg = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
            px = fg.load()
            for y in range(fg.height):
                for x in range(fg.width):
                    r, g, b, _ = px[x, y]
                    if r > 245 and g > 245 and b > 245:
                        px[x, y] = (0, 0, 0, 0)
            img = Image.new("RGBA", fg.size, BG + (255,))
            img.alpha_composite(fg)
            pad = 24
            framed = Image.new("RGB", (img.width + 2 * pad, img.height + 2 * pad), BG)
            framed.paste(img.convert("RGB"), (pad, pad))
            framed.save(IMG / f"{name}.png", optimize=True)
            print(name, framed.size)


def detail(width: int = 4800, box: tuple[float, float, float, float] = (0.44, 0.0, 0.78, 1.0)) -> None:
    """Close-up of the AD9288 -> RP2354B QFN-80 data buses (x about 61..108 mm)."""
    from PIL import Image
    global LAYERS
    saved, LAYERS = LAYERS, {"layer_detail": "B.Cu,In2.Cu,F.Cu,F.Silkscreen,Edge.Cuts"}
    try:
        layers(width)
    finally:
        LAYERS = saved
    path = IMG / "layer_detail.png"
    im = Image.open(path)
    w, h = im.size
    im = im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
    im.thumbnail((1400, 700))
    im.save(path, optimize=True)
    print("layer_detail", im.size)


RENDERS = {
    "board_iso": ["--side", "top", "--rotate", "-52,0,18", "--zoom", "1.35"],
    "board_iso2": ["--side", "top", "--rotate", "-55,0,-18", "--zoom", "1.35"],
    "board_top": ["--side", "top", "--zoom", "1.40"],
    "board_bottom": ["--side", "bottom", "--zoom", "1.40"],
    "board_front": ["--side", "front", "--rotate", "15,0,0", "--zoom", "1.35"],
}


def render(w: int = 2800, h: int = 1100) -> None:
    from PIL import Image
    for name, args in RENDERS.items():
        path = IMG / f"{name}.png"
        # the copy with DNP flags cleared (docs/render_board.py), so hand-fitted parts show
        kcli("pcb", "render", "--quality", "high", "--background", "transparent",
             "-w", w, "-h", h, *args, "-o", path, PCB.with_name(".render.kicad_pcb"))
        im = Image.open(path).convert("RGBA")
        a = im.getchannel("A").point(lambda v: 0 if v < 180 else min(255, (v - 180) * 4))
        im.putalpha(a)
        bbox = a.getbbox()
        if bbox:
            im = im.crop(bbox)
        im.save(path, optimize=True)
        print(name, im.size)


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    jobs = sys.argv[1:] or ["sch", "layers", "render", "case"]
    if "sch" in jobs:
        schematic()
    if "layers" in jobs:
        layers()
        detail()
    if "render" in jobs:
        render()
    if "case" in jobs:
        from case_views import case_views
        case_views(IMG)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
