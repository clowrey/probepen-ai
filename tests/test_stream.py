"""USB streaming: firmware/src/stream.c (plan, blocks, gaps), the STREAM command, the host decoder, and
whole streams through the simulated board (sim/sim.c) with a limited USB budget."""
from __future__ import annotations

import ctypes
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"
sys.path.insert(0, str(ROOT / "host"))
from probepen import StreamDecoder, parse_kv  # noqa: E402

EMIT = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_void_p)


class Stream(ctypes.Structure):
    _fields_ = [
        ("chmask", ctypes.c_uint8), ("clkdiv", ctypes.c_uint32), ("avg", ctypes.c_uint32),
        ("raw_hz", ctypes.c_float), ("rate_hz", ctypes.c_float), ("range_flags", ctypes.c_uint8),
        ("acc", ctypes.c_uint32 * 2), ("nacc", ctypes.c_uint32), ("out_index", ctypes.c_uint32),
        ("block_first", ctypes.c_uint32), ("lost", ctypes.c_bool), ("block", ctypes.c_uint8 * 1036),
        ("fill", ctypes.c_size_t), ("blocks", ctypes.c_uint32), ("lost_events", ctypes.c_uint32),
        ("lost_samples", ctypes.c_uint32), ("bytes", ctypes.c_uint64),
    ]


def _build(tmp, name, extra=()):
    if not shutil.which("gcc"):
        pytest.skip("gcc not installed")
    so = tmp / name
    srcs = [*extra] + [str(s) for s in sorted((FW / "src").glob("*.c")) if s.name != "main.c"]
    subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-D_DEFAULT_SOURCE", "-shared", "-fPIC",
                    f"-I{FW / 'include'}", *srcs, "-lm", "-o", str(so)], check=True)
    return ctypes.CDLL(str(so))


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    lib = _build(tmp_path_factory.mktemp("stream"), "libcore.so")
    lib.stream_plan.argtypes = [ctypes.POINTER(Stream), ctypes.c_float, ctypes.c_uint8, ctypes.c_float, ctypes.c_uint8]
    lib.stream_plan.restype = ctypes.c_bool
    lib.stream_push_words.argtypes = [ctypes.POINTER(Stream), ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t, EMIT,
                                      ctypes.c_void_p]
    lib.stream_skip_raw.argtypes = [ctypes.POINTER(Stream), ctypes.c_uint32, EMIT, ctypes.c_void_p]
    lib.stream_flush.argtypes = [ctypes.POINTER(Stream), EMIT, ctypes.c_void_p]
    return lib


def rbit8(v):
    return int(f"{v:08b}"[::-1], 2)


def word(a, b):
    """PIO word for tip codes a, b (the front end inverts; channel A is bit-reversed)."""
    return rbit8(255 - a) | ((255 - b) << 16) | 0x5A00A500


def test_plan_picks_divider_and_averaging(core):
    s = Stream()
    assert core.stream_plan(ctypes.byref(s), 100e3, 3, 75e6, 0)
    assert (s.clkdiv, s.avg) == (750, 1) and abs(s.rate_hz - 100e3) < 1
    assert core.stream_plan(ctypes.byref(s), 100.0, 1, 75e6, 0)          # slower than 75 MHz / 65535
    assert s.avg == 12 and s.clkdiv == 62500 and abs(s.rate_hz - 100.0) < 0.01
    assert core.stream_plan(ctypes.byref(s), 75e6, 2, 75e6, 0) and s.clkdiv == 1
    assert not core.stream_plan(ctypes.byref(s), 0.0, 3, 75e6, 0)
    assert not core.stream_plan(ctypes.byref(s), 1e3, 4, 75e6, 0)


def test_blocks_decode_with_the_host_decoder(core):
    s = Stream()
    core.stream_plan(ctypes.byref(s), 1e6, 3, 75e6, 0x04)
    out = bytearray()
    emit = EMIT(lambda p, n, c: out.extend(ctypes.string_at(p, n)))
    n = 1500
    a = [(i * 7) % 256 for i in range(n)]
    b = [(255 - i) % 256 for i in range(n)]
    words = (ctypes.c_uint32 * n)(*(word(x, y) for x, y in zip(a, b)))
    core.stream_push_words(ctypes.byref(s), words, n, emit, None)
    core.stream_flush(ctypes.byref(s), emit, None)
    dec = StreamDecoder()
    events = dec.feed(b"OK RATE=1\n" + bytes(out) + b"STREAM END\n")
    assert events[0] == ("line", "OK RATE=1") and events[-1] == ("line", "STREAM END")
    blocks = [e for e in events if e[0] == "block"]
    assert [e[3] for e in blocks] == [0, 512, 1024]                  # first-sample indices
    assert all(e[1] == 0x04 and e[2] == 3 for e in blocks)          # CH2 on +-26 V, both channels
    payload = b"".join(e[4] for e in blocks)
    assert list(payload[0::2]) == a and list(payload[1::2]) == b
    assert dec.crc_errors == 0 and dec.lost_samples == 0 and dec.samples == n
    # a corrupted byte costs one block, and the decoder finds the next one
    bad = bytearray(out)
    bad[20] ^= 0xFF
    dec2 = StreamDecoder()
    got = [e for e in dec2.feed(bytes(bad)) if e[0] == "block"]
    assert dec2.crc_errors >= 1 and len(got) == 2


def test_averaging_and_gaps(core):
    s = Stream()
    core.stream_plan(ctypes.byref(s), 100.0, 1, 75e6, 0)              # avg 12
    out = bytearray()
    emit = EMIT(lambda p, n, c: out.extend(ctypes.string_at(p, n)))
    words = (ctypes.c_uint32 * 120)(*([word(100, 0)] * 6 + [word(101, 0)] * 6) * 10)
    core.stream_push_words(ctypes.byref(s), words, 120, emit, None)  # 10 samples of 100.5 -> rounds to 101
    core.stream_skip_raw(ctypes.byref(s), 1200, emit, None)          # 100 samples lost
    core.stream_push_words(ctypes.byref(s), words, 120, emit, None)
    core.stream_flush(ctypes.byref(s), emit, None)
    dec = StreamDecoder()
    blocks = [e for e in dec.feed(bytes(out)) if e[0] == "block"]
    assert [(e[1] & 1, e[3], len(e[4])) for e in blocks] == [(0, 0, 10), (1, 110, 10)]
    assert set(blocks[0][4]) == {101}
    assert dec.lost_samples == 100 and s.lost_samples == 100 and s.lost_events == 1


def test_stream_command(core):
    cmd_t = type("UsbCmd", (ctypes.Structure,), {"_fields_": [
        ("kind", ctypes.c_int), ("arg_i", ctypes.c_int32), ("arg_f", ctypes.c_float), ("valid", ctypes.c_bool),
        ("arg_i2", ctypes.c_int32)]})
    core.usb_parse_command.restype = cmd_t
    c = core.usb_parse_command(b"STREAM 250000 1")
    assert (c.kind, c.arg_i, c.valid) == (11, 1, True) and c.arg_f == 250000.0
    c = core.usb_parse_command(b"stream 1e6")
    assert (c.arg_i, c.valid) == (3, True) and c.arg_f == 1e6
    assert core.usb_parse_command(b"STREAM 0").valid
    assert not core.usb_parse_command(b"STREAM 1000 7").valid
    assert not core.usb_parse_command(b"STREAM x").valid


# ---- whole streams through the simulated board ---------------------------------------------------

@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    lib = _build(tmp_path_factory.mktemp("sims"), "libsim.so", [str(ROOT / "sim" / "sim.c")])
    lib.sim_step.argtypes = [ctypes.c_double]
    lib.sim_info.restype = ctypes.c_char_p
    lib.sim_usb_in_buf.restype = ctypes.c_void_p
    lib.sim_usb_out.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.sim_set_usb_budget.argtypes = [ctypes.c_double]
    return lib


def run_stream(sim, rate, chmask, budget, ms=1000.0, step=10.0):
    sim.sim_init()
    sim.sim_set_usb_budget(budget)
    sim.sim_set_signal.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_float] * 5
    sim.sim_set_signal(0, 2, 3.0, 0.0, 1e3, 0.5, 0.01)              # CH1: 3 Vpp 1 kHz sine
    t = 0.0
    dec = StreamDecoder()

    def cmd(line):
        buf = line.encode() + b"\0"
        ctypes.memmove(sim.sim_usb_in_buf(), buf, len(buf))
        sim.sim_usb_in_commit()

    def drain():
        n = sim.sim_usb_out_len()
        ev = dec.feed(bytes(sim.sim_usb_out()[:n]))
        sim.sim_usb_out_clear()
        return ev

    for _ in range(20):
        t += 10.0
        sim.sim_step(ctypes.c_double(t))
    drain()
    cmd(f"STREAM {rate} {chmask}")
    reply = [e for e in drain() if e[0] == "line"][0][1]
    events = []
    t0 = t
    while t - t0 < ms:
        t += step
        sim.sim_step(ctypes.c_double(t))
        events += drain()
    cmd("STREAM 0")
    t += 10.0
    sim.sim_step(ctypes.c_double(t))
    events += drain()
    return reply, dec, events


def test_sim_stream_within_budget(sim):
    reply, dec, events = run_stream(sim, 100e3, 3, 1.0e6)
    plan = parse_kv(reply)
    assert reply.startswith("OK") and plan["RATE"] == pytest.approx(100e3) and plan["BPS"] == 200000
    assert dec.lost_samples == 0 and dec.crc_errors == 0
    assert dec.samples == pytest.approx(100e3, rel=0.03)            # one second of samples, per channel
    assert any(e[0] == "line" and e[1].startswith("STREAM END") for e in events)
    # CH1 is the default 3 Vpp sine: about 77 codes peak to peak
    ch1 = b"".join(e[4] for e in events if e[0] == "block")[0::2]
    assert 65 < max(ch1) - min(ch1) < 90


def test_sim_stream_over_budget_reports_losses(sim):
    reply, dec, _ = run_stream(sim, 1e6, 3, 1.0e6)                     # 2 MB/s asked, 1 MB/s available
    assert reply.startswith("OK")
    assert dec.lost_samples > 100e3                                    # about half the samples cannot go out
    assert dec.samples * 2 == pytest.approx(1.0e6, rel=0.05)           # what fits the budget arrives intact
    assert dec.crc_errors == 0


def test_sim_slow_stream_averages(sim):
    reply, dec, events = run_stream(sim, 1000, 1, 1.0e6, ms=2000)
    plan = parse_kv(reply)
    assert plan["AVG"] >= 1 and dec.samples == pytest.approx(2000, rel=0.05) and dec.lost_samples == 0
    assert math.isclose(plan["RATE"], 1000, rel_tol=1e-3)


def test_sim_stream_survives_seldom_page_frames(sim):
    """A background browser tab steps the model only now and then: the pen still drains its ring."""
    _, dec, _ = run_stream(sim, 200e3, 3, 1.0e6, ms=2000, step=500.0)
    assert dec.lost_samples == 0 and dec.samples == pytest.approx(300e3, rel=0.03)   # starts at the first step: 1.5 s
