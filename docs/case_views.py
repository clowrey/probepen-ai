"""3D views of the build123d pen case with the populated board inside, rendered offscreen with VTK.

    .venv/bin/python docs/case_views.py

Writes docs/img/case_assembled.png (closed case from above and from below, LCD visible),
docs/img/case_parts.png (exploded, and each shell and the board on its own) and the top-down shots
docs/img/case_top_down.png / case_bottom_up.png used by docs/composite.py.
The board comes from hw/.render.kicad_pcb (docs/render_board.py), so the hand-fitted DNP parts show.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import vtk
from PIL import Image, ImageDraw, ImageFont
from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from case import case as casemod  # noqa: E402

KCLI = shutil.which("kicad-cli") or str(Path.home() / ".local" / "bin" / "kicad-cli")
PCB = ROOT / "hw" / ".render.kicad_pcb"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

TOP_COL = (0.86, 0.87, 0.90)
BOT_COL = (0.28, 0.32, 0.38)


def shape_actor(shape, colour, tol: float = 0.04, opacity: float = 1.0) -> vtk.vtkActor:
    verts, tris = shape.tessellate(tol, 0.2)
    pts = np.array([(v.X, v.Y, v.Z) for v in verts], dtype=float)
    cells = np.hstack([np.full((len(tris), 1), 3), np.array(tris)]).astype(np.int64).ravel()
    poly = vtk.vtkPolyData()
    p = vtk.vtkPoints()
    p.SetData(numpy_to_vtk(pts, deep=True))
    poly.SetPoints(p)
    ca = vtk.vtkCellArray()
    ca.SetCells(len(tris), numpy_to_vtkIdTypeArray(cells, deep=True))
    poly.SetPolys(ca)
    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(poly)
    normals.SetFeatureAngle(35)
    normals.SplittingOn()
    normals.ConsistencyOn()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(normals.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    pr = actor.GetProperty()
    pr.SetColor(*colour)
    pr.SetOpacity(opacity)
    pr.SetAmbient(0.18)
    pr.SetDiffuse(0.78)
    pr.SetSpecular(0.18)
    pr.SetSpecularPower(24)
    return actor


def board_actors(glb: Path) -> list[vtk.vtkActor]:
    ren = vtk.vtkRenderer()
    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.AddRenderer(ren)
    imp = vtk.vtkGLTFImporter()
    imp.SetFileName(str(glb))
    imp.SetRenderWindow(win)
    imp.Update()
    actors = []
    coll = ren.GetActors()
    coll.InitTraversal()
    for _ in range(coll.GetNumberOfItems()):
        actors.append(coll.GetNextActor())
    for a in actors:
        ren.RemoveActor(a)
    return actors


def shift(actors, dx=0.0, dy=0.0, dz=0.0, scale=1.0):
    for a in actors:
        t = vtk.vtkTransform()
        t.PostMultiply()
        if a.GetUserTransform() is not None:
            t.Concatenate(a.GetUserTransform())
        if scale != 1.0:
            t.RotateX(90)
            t.Scale(scale, scale, scale)
        t.Translate(dx, dy, dz)
        a.SetUserTransform(t)
    return actors


def render(actors, direction, up=(0, 0, 1), size=(2400, 1000), zoom=1.0, angle=18, parallel=False) -> Image.Image:
    ren = vtk.vtkRenderer()
    ren.SetBackground(1, 1, 1)
    ren.SetBackgroundAlpha(0.0)
    for a in actors:
        ren.AddActor(a)
    kit = vtk.vtkLightKit()
    kit.SetKeyLightIntensity(0.95)
    kit.AddLightsToRenderer(ren)
    ren.UseFXAAOn()
    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.SetAlphaBitPlanes(1)
    win.SetMultiSamples(0)
    win.SetSize(*size)
    win.AddRenderer(ren)
    cam = ren.GetActiveCamera()
    cam.SetViewAngle(angle)
    cam.SetFocalPoint(0, 0, 0)
    cam.SetPosition(*direction)
    cam.SetViewUp(*up)
    ren.ResetCamera()
    if parallel:   # straight down (or up) the z axis: fit the x/y extent to the window
        cam.ParallelProjectionOn()
        b = ren.ComputeVisiblePropBounds()
        cam.SetParallelScale(max(b[3] - b[2], (b[1] - b[0]) * size[1] / size[0]) / 2 * 1.02)
    cam.Zoom(zoom)
    ren.ResetCameraClippingRange()
    win.Render()
    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(win)
    grab.SetInputBufferTypeToRGBA()
    grab.ReadFrontBufferOff()
    grab.Update()
    w, h, _ = grab.GetOutput().GetDimensions()
    from vtk.util.numpy_support import vtk_to_numpy
    arr = vtk_to_numpy(grab.GetOutput().GetPointData().GetScalars()).reshape(h, w, 4)[::-1]
    im = Image.fromarray(np.ascontiguousarray(arr), "RGBA")
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


def screen_actor(feat: dict, cx: float) -> list[vtk.vtkActor]:
    """The firmware's own CH1+2 screen (docs/fw_ui_preview.py) on the LCD's visible area, facing down.
    Mapped so it reads the right way round when the pen is turned over about its long axis."""
    png = ROOT / "docs" / "img" / "ui_screen.png"
    if not png.is_file():
        return []
    gx = [v for v in feat["lcd_window"]]
    x0, y0, x1, y1 = gx[0] - cx, gx[1], gx[2] - cx, gx[3]
    z = -1.92   # just below the LCD model's face (-1.895: 0.2 mm pads, 1.6 mm glass, KiCad's mask/copper)
    plane = vtk.vtkPlaneSource()
    plane.SetOrigin(x0, y1, z)
    plane.SetPoint1(x1, y1, z)
    plane.SetPoint2(x0, y0, z)
    reader = vtk.vtkPNGReader()
    reader.SetFileName(str(png))
    tex = vtk.vtkTexture()
    tex.SetInputConnection(reader.GetOutputPort())
    tex.InterpolateOn()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(plane.GetOutputPort())
    a = vtk.vtkActor()
    a.SetMapper(mapper)
    a.SetTexture(tex)
    a.GetProperty().SetAmbient(0.9)
    a.GetProperty().SetDiffuse(0.2)
    a.GetProperty().LightingOff()
    return [a]


def label(im: Image.Image, text: str, size: int = 30) -> Image.Image:
    """Panel with a caption line above the image, white background."""
    font = ImageFont.truetype(FONT, size)
    pad = size + 14
    out = Image.new("RGBA", (im.width, im.height + pad), (255, 255, 255, 255))
    ImageDraw.Draw(out).text((4, 2), text, fill=(40, 46, 58, 255), font=font)
    out.alpha_composite(im, (0, pad))
    return out


def stack(ims: list[Image.Image], gap: int = 16, width: int | None = None) -> Image.Image:
    width = width or max(i.width for i in ims)
    ims = [i if i.width <= width else i.resize((width, int(i.height * width / i.width))) for i in ims]
    out = Image.new("RGBA", (width, sum(i.height for i in ims) + gap * (len(ims) - 1)), (255, 255, 255, 255))
    y = 0
    for i in ims:
        out.alpha_composite(i, ((width - i.width) // 2, y))
        y += i.height + gap
    return out


def row(ims: list[Image.Image], gap: int = 24) -> Image.Image:
    h = max(i.height for i in ims)
    out = Image.new("RGBA", (sum(i.width for i in ims) + gap * (len(ims) - 1), h), (255, 255, 255, 255))
    x = 0
    for i in ims:
        out.alpha_composite(i, (x, (h - i.height) // 2))
        x += i.width + gap
    return out


def case_views(img_dir: Path) -> None:
    feat = json.loads((ROOT / "hw" / "out" / "board_features.json").read_text(encoding="utf8"))
    parts = casemod.build_parts(feat)
    top, bot = parts["top_shell"], parts["bottom_shell"]
    length, width = feat["outline_mm"]
    cx, hw_y = length / 2.0, width / 2.0

    with tempfile.TemporaryDirectory() as tmp:
        glb = Path(tmp) / "board.glb"
        subprocess.run(
            [str(KCLI), "pcb", "export", "glb", "--drill-origin", "--subst-models", "--include-tracks",
             "--include-pads", "--include-zones", "--include-silkscreen", "--include-soldermask",
             "-o", str(glb), str(PCB)],
            check=True, capture_output=True,
        )
        raw = shift(board_actors(glb), dx=-cx, dy=-hw_y, scale=1000.0)
        # the board's own slab: the lowest actor whose height is about the board thickness
        slabs = [a for a in raw if 1.4 < a.GetBounds()[5] - a.GetBounds()[4] < 1.8]
        z_shift = -min(a.GetBounds()[4] for a in slabs) if slabs else 0.0

        def board(dz=0.0):
            scr = screen_actor(feat, cx)
            for a in scr:
                t = vtk.vtkTransform()
                t.Translate(0, 0, dz)
                a.SetUserTransform(t)
            return shift(board_actors(glb), dx=-cx, dy=-hw_y, dz=z_shift + dz, scale=1000.0) + scr

        def shell(shape, col, dz=0.0):
            return shift([shape_actor(shape, col)], dx=-cx, dz=dz)

        closed = lambda: shell(bot, BOT_COL) + board() + shell(top, TOP_COL)  # noqa: E731

        # top-down and bottom-up, orthographic, for the board composite
        render(closed(), (0, 0, 1), up=(0, 1, 0), parallel=True, size=(3000, 600)).save(img_dir / "case_top_down.png")
        # screen side up: turned over about the long axis, so the nose stays on the left and the LCD reads
        render(closed(), (0, 0, -1), up=(0, -1, 0), parallel=True, size=(3000, 600)).save(img_dir / "case_bottom_up.png")

        # assembled: from above (WAGO, buttons, USB) and from below (LCD through its window)
        a1 = label(render(closed(), (-0.45, -0.80, 0.60), zoom=1.25), "Closed case, from above")
        a2 = label(render(closed(), (-0.40, 0.75, -0.65), up=(0, 0, -1), zoom=1.25), "From below: the LCD window")
        stack([a1, a2], gap=10).convert("RGB").save(img_dir / "case_assembled.png", optimize=True)

        # parts sheet
        p_exp = label(render(shell(bot, BOT_COL, -14.0) + board() + shell(top, TOP_COL, 14.0),
                             (-0.60, -0.80, 0.55), size=(2400, 1300), zoom=1.15), "Exploded")
        p_top_in = label(render(shell(top, TOP_COL), (-0.30, 0.60, -0.75), up=(0, 0, -1), zoom=1.2),
                         "Top shell, inside (flexure button tabs on the near wall)")
        p_top_out = label(render(shell(top, TOP_COL), (-0.45, -0.80, 0.60), zoom=1.2), "Top shell, outside")
        p_bot_in = label(render(shell(bot, BOT_COL), (-0.45, -0.75, 0.75), zoom=1.2),
                         "Bottom shell, inside (LCD ledge and window)")
        p_bot_out = label(render(shell(bot, BOT_COL), (-0.40, 0.75, -0.65), up=(0, 0, -1), zoom=1.2),
                          "Bottom shell, outside")
        p_pcb_top = label(render(board(), (-0.45, -0.80, 0.70), zoom=1.2), "Board, top")
        p_pcb_bot = label(render(board(), (-0.40, 0.75, -0.65), up=(0, 0, -1), zoom=1.2),
                          "Board, bottom (LCD, work-lights and trimmers fitted)")
        sheet = stack([p_exp, row([p_top_out, p_top_in]), row([p_bot_in, p_bot_out]), row([p_pcb_top, p_pcb_bot])],
                      gap=24, width=2400)
        sheet.convert("RGB").save(img_dir / "case_parts.png", optimize=True)
    for n in ("case_top_down", "case_bottom_up", "case_assembled", "case_parts"):
        print(n, Image.open(img_dir / f"{n}.png").size)


if __name__ == "__main__":
    out = ROOT / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    case_views(out)
