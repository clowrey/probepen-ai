"""3D views of the build123d pen enclosure with the PCB inside, rendered offscreen with VTK.

    .venv/bin/python docs/case_views.py
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
from PIL import Image
from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from case import case as casemod  # noqa: E402

KCLI = shutil.which("kicad-cli") or str(Path.home() / ".local" / "bin" / "kicad-cli")
PCB = ROOT / "hw" / "probepen_ai.kicad_pcb"

TOP_COL = (0.86, 0.87, 0.90)
BOT_COL = (0.28, 0.32, 0.38)
PCB_COL = (0.12, 0.42, 0.24)


def shape_actor(shape, colour: tuple[float, float, float], tol: float = 0.04) -> vtk.vtkActor:
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


def shift(actors: list[vtk.vtkActor], dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, scale: float = 1.0):
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


def render(actors: list[vtk.vtkActor], path: Path, direction, up=(0, 0, 1), size=(2200, 1000), zoom=1.15, angle=18):
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
    cam.Zoom(zoom)
    ren.ResetCameraClippingRange()
    win.Render()

    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(win)
    grab.SetInputBufferTypeToRGBA()
    grab.ReadFrontBufferOff()
    grab.Update()
    w = vtk.vtkPNGWriter()
    w.SetFileName(str(path))
    w.SetInputConnection(grab.GetOutputPort())
    w.Write()

    im = Image.open(path).convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im.crop(bbox).save(path, optimize=True)
    print(path.name, Image.open(path).size)


def case_views(img_dir: Path) -> None:
    feat = json.loads((ROOT / "hw" / "out" / "board_features.json").read_text(encoding="utf8"))
    parts = casemod.build_parts(feat)
    top = parts["top_shell"]
    bot = parts["bottom_shell"]
    length, width = feat["outline_mm"]
    hw_y = width / 2.0
    cx = length / 2.0

    with tempfile.TemporaryDirectory() as tmp:
        glb = Path(tmp) / "board.glb"
        subprocess.run(
            [
                str(KCLI), "pcb", "export", "glb", "--drill-origin", "--subst-models",
                "--include-tracks", "--include-pads", "--include-zones", "--include-silkscreen",
                "--include-soldermask", "-o", str(glb), str(PCB),
            ],
            check=True, capture_output=True,
        )

        # Measure GLB Z bottom so PCB sits cleanly at Z = 0.0 inside case
        raw_acts = shift(board_actors(glb), dx=-cx, dy=-hw_y, scale=1000.0)
        z_mins = [a.GetBounds()[4] for a in raw_acts if a.GetBounds()[5] - a.GetBounds()[4] > 0.1]
        z_shift = -min(z_mins) if z_mins else 0.0

        def make_board(dz: float = 0.0):
            return shift(board_actors(glb), dx=-cx, dy=-hw_y, dz=z_shift + dz, scale=1000.0)

        def make_shell(shape, col, dz: float = 0.0):
            return shift([shape_actor(shape, col)], dx=-cx, dy=0.0, dz=dz)

        # 1. Closed enclosure from front-top isometric angle
        acts = make_shell(bot, BOT_COL) + make_board() + make_shell(top, TOP_COL)
        render(acts, img_dir / "case_front.png", direction=(-0.55, -0.85, 0.70), zoom=1.25)

        # 2. Exploded Z stack
        acts = make_shell(bot, BOT_COL, dz=-11.0) + make_board(dz=0.0) + make_shell(top, TOP_COL, dz=13.0)
        render(acts, img_dir / "case_exploded.png", direction=(-0.60, -0.80, 0.55), size=(2200, 1200), zoom=1.20)

        # 3. PCB seated inside bottom shell (top shell removed)
        acts = make_shell(bot, BOT_COL) + make_board()
        render(acts, img_dir / "case_inside.png", direction=(-0.45, -0.75, 0.85), zoom=1.25)

        # 4. Side/tail angle highlighting J3 JST-SH logic pod opening and J1 USB-C tail
        acts = make_shell(bot, BOT_COL) + make_board() + make_shell(top, TOP_COL)
        render(acts, img_dir / "case_tail_side.png", direction=(0.65, 0.85, 0.55), zoom=1.25)


if __name__ == "__main__":
    out = ROOT / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    case_views(out)
