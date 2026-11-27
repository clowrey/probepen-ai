"""The web simulator's board (sim/sim.c) around the firmware, built natively: captures, USB, buttons, CAL."""
from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"
sys.path.insert(0, str(ROOT / "host"))
from probepen import decode_frame  # noqa: E402


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    if not shutil.which("gcc"):
        pytest.skip("gcc not installed")
    so = tmp_path_factory.mktemp("sim") / "libsim.so"
    srcs = [str(ROOT / "sim" / "sim.c")] + [str(s) for s in sorted((FW / "src").glob("*.c")) if s.name != "main.c"]
    subprocess.run(["gcc", "-std=c11", "-O2", "-D_DEFAULT_SOURCE", "-shared", "-fPIC", f"-I{FW / 'include'}", *srcs,
                    "-lm", "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.sim_step.argtypes = [ctypes.c_double]
    lib.sim_info.restype = ctypes.c_char_p
    lib.sim_usb_in_buf.restype = ctypes.c_void_p
    lib.sim_usb_out.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.sim_fb.restype = ctypes.POINTER(ctypes.c_uint16)
    lib.sim_set_signal.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_float] * 5
    lib.sim_set_pod.argtypes = [ctypes.c_int, ctypes.c_float]
    return lib


class Dev:
    def __init__(self, lib):
        self.lib, self.t = lib, 0.0
        lib.sim_init()

    def run(self, ms: float, step: float = 10.0) -> None:
        end = self.t + ms
        while self.t < end:
            self.t += step
            self.lib.sim_step(ctypes.c_double(self.t))

    def info(self) -> dict:
        return json.loads(self.lib.sim_info().decode())

    def out(self) -> bytes:
        n = self.lib.sim_usb_out_len()
        data = bytes(self.lib.sim_usb_out()[:n])
        self.lib.sim_usb_out_clear()
        return data

    def cmd(self, line: str) -> str:
        buf = line.encode() + b"\0"
        ctypes.memmove(self.lib.sim_usb_in_buf(), buf, len(buf))
        self.lib.sim_usb_in_commit()
        return self.out().decode().strip()


def test_idn_and_default_capture(sim):
    d = Dev(sim)
    assert d.cmd("*IDN?").startswith("ProbePen-AI")
    d.run(200)
    info = d.info()
    assert info["mode"] == "CH1+2" and info["fs"] == 5e6 and info["triggered"] == 1
    fb = sim.sim_fb()
    assert len({fb[i] for i in range(0, 240 * 135, 7)}) > 4     # something was drawn


def test_capture_frames_decode_and_measure(sim):
    d = Dev(sim)
    d.run(200)
    assert d.cmd("GAIN 1") == "OK"
    d.run(200)
    reply = d.cmd("CAPTURE 1000")
    assert reply.startswith("OK")
    d.run(30)
    data = d.out()
    frames = []
    while data:
        count = data[3] | (data[4] << 8)
        frames.append(decode_frame(data[:7 + 2 * count]))
        data = data[7 + 2 * count:]
    assert [s for s, _ in frames] == [0, 1]
    a = frames[0][1]
    # CH1 is a 3 Vpp sine on the +-5 V range: about 3 / 0.0392 = 77 codes peak to peak, centred near 128
    assert 65 < max(a) - min(a) < 90 and 115 < sum(a) / len(a) < 140


def test_peak_detect_and_logic(sim):
    d = Dev(sim)
    assert d.cmd("TB 11").startswith("OK")        # 500 us/div: peak detect
    d.run(300)
    assert d.info()["peak_m"] > 0
    assert d.cmd("MODE 3") == "OK"                 # logic pod
    sim.sim_set_pod(0, 1e5)
    d.run(300)
    assert d.info()["mode"] == "LOGIC"


def test_cal_zeroes_offset(sim):
    d = Dev(sim)
    for ch in (0, 1):
        sim.sim_set_signal(ch, 0, 0, 0, 1, 0.5, 0)  # tips grounded
    d.cmd("GAIN 1")
    d.run(200)
    assert d.cmd("CAL") == "OK"
    d.run(200)
    info = d.info()
    assert info["flash_writes"] == 1 and abs(info["off1"]) > 0.005


def test_committed_wasm_matches_the_sources():
    """sim/site/probepen.wasm is committed for GitHub Pages: rebuild it (sim/build.sh) after firmware edits."""
    import hashlib
    srcs = [ROOT / "sim" / "sim.c"] + [s for s in (FW / "src").glob("*.c") if s.name != "main.c"]
    srcs += list((FW / "include").glob("*.h"))
    h = hashlib.sha256()
    for p in sorted(str(s.relative_to(ROOT)) for s in srcs):
        h.update(p.encode() + b"\0" + (ROOT / p).read_bytes())
    built = (ROOT / "sim" / "site" / "build.txt").read_text().split()[1]
    assert built == h.hexdigest(), "sim/site/probepen.wasm is stale: run sim/build.sh"


def test_held_up_button_moves_trigger(sim):
    d = Dev(sim)
    d.run(100)
    level = d.info()["trig_level"]
    sim.sim_set_buttons(1 << 2)                     # UP held
    d.run(1000)
    sim.sim_set_buttons(0)
    d.run(100)
    assert d.info()["trig_level"] > level
