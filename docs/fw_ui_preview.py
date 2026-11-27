"""Render the firmware's screens on the host: builds firmware/src (minus main.c) as a shared library, feeds the
app synthetic captures through its C API, and saves the framebuffers as one PNG.

    .venv/bin/python docs/fw_ui_preview.py [--lcd096]
"""
from __future__ import annotations

import ctypes
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware"
OUT = ROOT / "docs" / "img" / "ui_screens.png"


def build(lcd096: bool, tmp: Path) -> ctypes.CDLL:
    so = tmp / "libpp.so"
    srcs = [str(s) for s in sorted((FW / "src").glob("*.c")) if s.name != "main.c"]
    cmd = ["gcc", "-std=c11", "-O2", "-shared", "-fPIC", f"-I{FW / 'include'}", *srcs, "-lm", "-o", str(so)]
    if lcd096:
        cmd.insert(1, "-DPROBEPEN_LCD_096")
    subprocess.run(cmd, check=True)
    return ctypes.CDLL(str(so))


def lcd_size(lcd096: bool) -> tuple[int, int]:
    return (160, 80) if lcd096 else (240, 135)


def rbit8(v: int) -> int:
    return int(f"{v:08b}"[::-1], 2)


def screens(lcd096: bool = False) -> list[Image.Image]:
    w, h = lcd_size(lcd096)
    with tempfile.TemporaryDirectory() as tmp:
        lib = build(lcd096, Path(tmp))
        app = ctypes.create_string_buffer(4096)
        reply = ctypes.create_string_buffer(512)
        lib.app_init(app)

        def cmd(s: str) -> str:
            lib.app_usb_command(app, s.encode(), reply, 512)
            return reply.value.decode()

        cmd("GAIN 1")
        cmd("TB 6")                               # 10 us/div -> 5 MS/s
        lib.app_capture_samples.restype = ctypes.c_size_t
        n = lib.app_capture_samples(app, ctypes.c_size_t(4096))
        fs = 5e6
        per_v = 256 / (9.803 * 1.024)              # codes per tip volt on the +-5 V range
        a = [int(128 + per_v * 1.5 * math.sin(2 * math.pi * 50e3 * i / fs) + 1.5 * math.sin(i * 1.7)) for i in range(n)]
        b = [int(128 + per_v * (3.3 if (i * 25e3 / fs) % 1.0 < 0.5 else 0.0)) for i in range(n)]
        ca = (ctypes.c_uint16 * n)(*a)
        cb = (ctypes.c_uint16 * n)(*b)
        nib = (ctypes.c_uint8 * n)(*[
            ((i // 25) % 2) | (((i // 50) % 2) << 1) | (((i * 7 // 90) % 3 == 0) << 2) | (1 << 3) for i in range(n)
        ])
        lib.app_set_dc(app, 1163, 1795, 3427)      # +1.234 V, -3.30 V, VBUS 5.02 V
        fb = (ctypes.c_uint16 * (w * h))()
        imgs = []
        for mode in (0, 1, 3, 4):
            cmd(f"MODE {mode}")
            if mode == 3:
                lib.app_process_logic(app, nib, ctypes.c_size_t(n))
            else:
                lib.app_process_scope(app, ca, cb, ctypes.c_size_t(n))
            lib.app_render(app, fb, ca, cb, ctypes.c_size_t(n), nib, ctypes.c_size_t(n))
            im = Image.new("RGB", (w, h))
            px = im.load()
            for y in range(h):
                for x in range(w):
                    v = fb[y * w + x]
                    px[x, y] = (((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31)
            imgs.append(im)
        return imgs


def sheet(imgs: list[Image.Image], scale: int = 3, gap: int = 12) -> Image.Image:
    w, h = imgs[0].size
    W, H = 2 * w * scale + 3 * gap, 2 * h * scale + 3 * gap
    out = Image.new("RGB", (W, H), (255, 255, 255))
    for k, im in enumerate(imgs):
        x = gap + (k % 2) * (w * scale + gap)
        y = gap + (k // 2) * (h * scale + gap)
        out.paste(im.resize((w * scale, h * scale), Image.NEAREST), (x, y))
    return out


if __name__ == "__main__":
    small = "--lcd096" in sys.argv
    img = sheet(screens(small))
    path = OUT.with_name("ui_screens_096.png") if small else OUT
    img.save(path, optimize=True)
    print(path, img.size)
