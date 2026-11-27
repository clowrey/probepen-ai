"""docs/img/board_layers.jpg: the README's top image. Top-down views only, all at one scale and lined up
at the nose: the four copper layers, all layers together, the populated board from above and below
(DNP parts fitted), and the closed case from above.

    kicad-python docs/render_board.py          # hw/.render.kicad_pcb with the DNP parts shown
    .venv/bin/python docs/make_images.py layers
    .venv/bin/python docs/case_views.py        # case_top_down.png
    .venv/bin/python docs/composite.py
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
PCB = ROOT / "hw" / ".render.kicad_pcb"
KCLI = shutil.which("kicad-cli") or str(Path.home() / ".local" / "bin" / "kicad-cli")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

BOARD_L = 138.0      # mm, nose to tail edge
LAYER_PAD = 24       # px of frame make_images.py puts round each layer image
USB_OVERHANG = 1.0   # mm the USB-C shell sticks out past the tail
CASE_X0, CASE_X1 = -1.95, 139.95   # mm, case extent along the board


def render(side: str, tmp: Path) -> Image.Image:
    out = tmp / f"{side}.png"
    subprocess.run([str(KCLI), "pcb", "render", "--quality", "high", "--background", "transparent",
                    "-w", "4000", "-h", "800", "--side", side, "--zoom", "1.45", "-o", str(out), str(PCB)],
                   check=True, capture_output=True)
    im = Image.open(out).convert("RGBA")
    a = im.getchannel("A").point(lambda v: 0 if v < 180 else min(255, (v - 180) * 4))
    im.putalpha(a)
    return im.crop(a.getbbox())


def main() -> None:
    layers = [("F.Cu", "layer_f_cu"), ("In1.Cu (ground)", "layer_in1_cu"),
              ("In2.Cu (ground and supply rails)", "layer_in2_cu"), ("B.Cu", "layer_b_cu"),
              ("All copper layers", "layer_all")]
    first = Image.open(IMG / f"{layers[0][1]}.png")
    ppm = (first.width - 2 * LAYER_PAD) / BOARD_L          # px per mm of the layer images
    x_nose = 60 + LAYER_PAD                                 # canvas x of the board's nose edge

    panels = []   # (caption, image, x offset of the image's left edge)
    for cap, name in layers:
        panels.append((cap, Image.open(IMG / f"{name}.png").convert("RGBA"), x_nose - LAYER_PAD))
    with tempfile.TemporaryDirectory() as tmp:
        top = render("top", Path(tmp))
        bot = render("bottom", Path(tmp))
    for cap, im, mirrored in (("Board, top (3D)", top, False), ("Board, bottom (3D): LCD, trimmers, work-lights", bot, True)):
        w = round((BOARD_L + USB_OVERHANG) * ppm)
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        if mirrored:
            # kicad-cli shows the underside with the nose on the right; turning it 180 degrees (the board
            # flipped over about its long axis) keeps the nose on the left like every other panel
            im = im.rotate(180)
        panels.append((cap, im, x_nose))
    case = Image.open(IMG / "case_top_down.png").convert("RGBA")
    w = round((CASE_X1 - CASE_X0) * ppm)
    case = case.resize((w, round(case.height * w / case.width)), Image.LANCZOS)
    panels.append(("Case, top", case, x_nose + round(CASE_X0 * ppm)))

    font = ImageFont.truetype(FONT, 22)
    cap_h, gap = 30, 14
    width = max(x + im.width for _, im, x in panels) + 60
    height = sum(cap_h + im.height + gap for _, im, _ in panels) + 20
    out = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    d = ImageDraw.Draw(out)
    y = 10
    for cap, im, x in panels:
        d.text((x_nose - LAYER_PAD, y), cap, fill=(40, 46, 58, 255), font=font)
        y += cap_h
        out.alpha_composite(im, (x, y))
        y += im.height + gap
    out.convert("RGB").save(IMG / "board_layers.jpg", quality=90, optimize=True)
    print("board_layers.jpg", out.size, f"{ppm:.2f} px/mm")


if __name__ == "__main__":
    main()
