"""AI mode: the pen's AI commands and screens (firmware/src/app.c), and the host side (host/ai_view.py,
host/probepen.py ai_serve) answering the simulated pen through a fake serial port, with the rules (no key,
no network)."""
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
import ai_view  # noqa: E402
from probepen import ProbePen  # noqa: E402


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    if not shutil.which("gcc"):
        pytest.skip("gcc not installed")
    so = tmp_path_factory.mktemp("ai") / "libsim.so"
    srcs = [str(ROOT / "sim" / "sim.c")] + [str(s) for s in sorted((FW / "src").glob("*.c")) if s.name != "main.c"]
    subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-D_DEFAULT_SOURCE", "-shared", "-fPIC",
                    f"-I{FW / 'include'}", *srcs, "-lm", "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.sim_step.argtypes = [ctypes.c_double]
    lib.sim_info.restype = ctypes.c_char_p
    lib.sim_usb_in_buf.restype = ctypes.c_void_p
    lib.sim_usb_out.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.sim_set_signal.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_float] * 5
    lib.sim_set_bitrate.argtypes = [ctypes.c_int, ctypes.c_float]
    lib.sim_fb.restype = ctypes.POINTER(ctypes.c_uint16)
    return lib


class SimPort:
    """A serial port onto the simulated pen: writes are command lines, each read steps it 16 ms."""

    def __init__(self, lib):
        self.lib, self.t, self.pending = lib, 0.0, b""
        lib.sim_init()

    def write(self, data: bytes) -> None:
        self.pending += data
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            b = line + b"\0"
            ctypes.memmove(self.lib.sim_usb_in_buf(), b, len(b))
            self.lib.sim_usb_in_commit()

    def step(self, ms: float) -> None:
        end = self.t + ms
        while self.t < end:
            self.t += 16.0
            self.lib.sim_step(ctypes.c_double(self.t))

    def read(self, n: int) -> bytes:
        self.step(16.0)
        k = self.lib.sim_usb_out_len()
        data = bytes(self.lib.sim_usb_out()[:k])
        self.lib.sim_usb_out_clear()
        return data

    def info(self) -> dict:
        return json.loads(self.lib.sim_info())


def ask(sim, sigs, rates=(0, 0)):
    port = SimPort(sim)
    for ch, s in enumerate(sigs):
        sim.sim_set_signal(ch, *s)
        sim.sim_set_bitrate(ch, rates[ch])
    pen = ProbePen(port)
    port.step(200)
    port.read(0)
    assert pen.command("MODE 6") == "OK"                       # AI mode: the pen sends an AIASK snapshot
    out = next(pen.ai_serve("rules"))
    port.step(300)
    return port, out


SINE = (2, 3.0, 0.0, 1e3, 0.5, 0.01)
GND = (0, 0, 0, 1, 0.5, 0)


def test_uart_is_decoded(sim):
    port, out = ask(sim, [(7, 3.3, 0, 115200, 0.5, 0.01), GND])
    assert out["plan"]["view"] == "decode_uart" and out["plan"]["decode_rate"] == 115200
    info = port.info()
    assert info["mode"] == "AI" and info["ai_state"] == 3 and info["ai_view"] == 5     # SHOWN, DECODE
    assert info["decoding"] == 1 and info["dec_rate"] == 115200 and info["dec_src"] == 1
    port.step(300)
    assert info["dec_errors"] == 0


def test_can_fd_is_decoded(sim):
    port, out = ask(sim, [(9, 0, 0, 500e3, 0.5, 0.02), (10, 0, 0, 500e3, 0.5, 0.02)], (2e6, 2e6))
    p = out["plan"]
    assert p["view"] == "decode_can" and p["decode_source"] == 3 and p["decode_data_rate"] == 2e6
    info = port.info()
    assert info["ai_view"] == 5 and info["decoding"] == 1 and info["dec_data"] == 2e6


def test_pwm_goes_to_the_meter_timing_page(sim):
    port, out = ask(sim, [(6, 12.0, 0, 20e3, 0.3, 0.03), GND])
    assert out["plan"]["view"] == "meter_timing"
    assert port.info()["ai_view"] == 4


def test_sine_goes_to_the_scope(sim):
    port, out = ask(sim, [SINE, GND])
    p = out["plan"]
    assert p["view"] == "scope_ch1" and ai_view.TB_NS[p["timebase_index"]] == 500e3   # about 4 periods of 1 kHz
    info = port.info()
    assert info["ai_view"] == 1 and info["tb_ns"] == 500e3


def test_quiet_tips_go_to_the_meter(sim):
    port, out = ask(sim, [(1, 0, 3.3, 1, 0.5, 0.005), GND])
    assert out["plan"]["view"] == "meter_volts"


def test_no_answer_is_said(sim):
    port = SimPort(sim)
    port.step(100)
    port.write(b"MODE 6\n")
    port.step(1000)
    assert port.info()["ai_state"] == 1                        # asking
    port.read(0)
    port.step(9000)
    assert port.info()["ai_state"] == 4                        # no answer: the screen says what to run


def test_ai_commands(sim):
    port = SimPort(sim)
    pen = ProbePen(port)
    port.step(100)
    port.read(0)
    assert pen.command("AI CLEAR") == "OK"
    assert pen.command("AI TITLE Hello") == "OK"
    assert pen.command("AI LINE " + "x" * 50) == "OK"          # wraps into two lines
    assert pen.command("AI VIEW 4 1") == "OK"                  # meter, TIMING page, without being asked
    reply = pen.command("AI?")
    assert reply.startswith("AI STATE=3 VIEW=4 PAGE=0 LINES=2")
    assert port.info()["mode"] == "AI"
    assert pen.command("AI VIEW 9") == "ERR"
    assert pen.command("MODE 0") == "OK" and port.info()["mode"] == "CH1+2"


def test_plan_to_commands_fits_the_pen():
    plan = {"view": "decode_can", "decode_source": 3, "decode_rate": 500e3, "decode_data_rate": 2e6,
            "title": "A very long title that goes on and on beyond the screen", "lines": ["é" * 80] * 9, "reason": ""}
    cmds = ai_view.plan_to_commands(plan)
    assert all(len(c) < 60 for c in cmds) and cmds[-1] == "AI VIEW 5 0"
    assert "DECODE CAN 3 500000 2000000" in cmds and sum(c.startswith("AI LINE") for c in cmds) == 6
    cmds = ai_view.plan_to_commands({"view": "nonsense", "title": "x", "lines": []})
    assert cmds[-1] == "AI VIEW 0 0"


def test_model_failure_falls_back_to_the_rules(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(ai_view, "ask_model", boom)
    snaps = [{"meas": "CH1 VPP=0 RANGE=1X;CH2 VPP=0 RANGE=1X;FS=1000000;TB=10000", "ch1": [128] * 100, "ch2": [128] * 100}]
    out = ai_view.ai_plan(snaps, "gemini", "a-key", "")
    assert out["source"] == "rules" and "no network" in out["note"] and out["plan"]["view"] == "meter_volts"


def test_key_file(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "cfg.json"
    f.write_text(json.dumps({"keys": {"gemini": "g-123", "anthropic": ""}}))
    assert ai_view.load_key("gemini", str(f)) == "g-123" and ai_view.load_key("anthropic", str(f)) == ""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-456")
    assert ai_view.load_key("anthropic", str(f)) == "a-456"
