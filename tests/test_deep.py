"""Deep record: firmware/src/deep.c (plan, decode, trigger), the DEEP command, and whole records through the
simulated board, checked with the host decoder."""
from __future__ import annotations

import ctypes
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "tests"))
from probepen import StreamDecoder, parse_kv  # noqa: E402
from test_stream import _build  # noqa: E402

DEEP_BYTES = 384 * 1024


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    lib = _build(tmp_path_factory.mktemp("deep"), "libcore.so")
    lib.deep_plan.argtypes = [ctypes.c_uint32, ctypes.c_uint8, ctypes.c_float, ctypes.c_float,
                              ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_float)]
    lib.deep_plan.restype = ctypes.c_bool
    lib.deep_max_samples.restype = ctypes.c_uint32
    lib.deep_find_trigger.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint8, ctypes.c_uint8,
                                      ctypes.c_bool]
    lib.deep_find_trigger.restype = ctypes.c_long
    lib.deep_decode.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_bool]
    return lib


def test_sizes_and_plan(core):
    assert core.deep_max_samples(3) == DEEP_BYTES // 2 == 196608     # both channels: 2.6 ms at 75 MS/s
    assert core.deep_max_samples(1) == core.deep_max_samples(2) == DEEP_BYTES == 393216
    n, k, r = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_float()
    assert core.deep_plan(0, 3, 75e6, 75e6, n, k, r) and (n.value, k.value, r.value) == (196608, 1, 75e6)
    assert core.deep_plan(10003, 1, 25e6, 75e6, n, k, r) and (n.value, k.value) == (10000, 3)
    assert not core.deep_plan(200000, 3, 75e6, 75e6, n, k, r)          # more than fits
    assert not core.deep_plan(0, 0, 75e6, 75e6, n, k, r)
    assert not core.deep_plan(0, 1, 80e6, 75e6, n, k, r)


def test_decode_and_trigger(core):
    rbit = lambda v: int(f"{v:08b}"[::-1], 2)  # noqa: E731
    codes = [10, 50, 200, 210, 90, 30, 150]
    raw_a = bytes(rbit(255 - c) for c in codes)
    buf = ctypes.create_string_buffer(raw_a, len(raw_a))
    core.deep_decode(buf, len(codes), True)
    assert list(buf.raw) == codes
    raw_b = bytes(255 - c for c in codes)
    buf = ctypes.create_string_buffer(raw_b, len(raw_b))
    core.deep_decode(buf, len(codes), False)
    assert list(buf.raw) == codes
    data = bytes(codes)
    assert core.deep_find_trigger(data, 7, 1, 128, 3, False) == 2     # rising through 128
    assert core.deep_find_trigger(data, 7, 3, 128, 3, False) == 6     # the next one
    assert core.deep_find_trigger(data, 7, 1, 128, 3, True) == 4      # falling
    assert core.deep_find_trigger(data, 7, 1, 250, 3, False) == -1


def test_deep_command_parse(core):
    cmd_t = type("UsbCmd", (ctypes.Structure,), {"_fields_": [
        ("kind", ctypes.c_int), ("arg_i", ctypes.c_int32), ("arg_f", ctypes.c_float), ("valid", ctypes.c_bool),
        ("arg_i2", ctypes.c_int32)]})
    core.usb_parse_command.restype = cmd_t
    c = core.usb_parse_command(b"DEEP")
    assert (c.kind, c.arg_i, c.arg_i2, c.arg_f, c.valid) == (12, 0, 3, 0.0, True)
    c = core.usb_parse_command(b"DEEP 50000 2 25e6")
    assert (c.arg_i, c.arg_i2, c.arg_f, c.valid) == (50000, 2, 25e6, True)
    assert not core.usb_parse_command(b"DEEP 100 4").valid


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    lib = _build(tmp_path_factory.mktemp("simdeep"), "libsim.so", [str(ROOT / "sim" / "sim.c")])
    lib.sim_step.argtypes = [ctypes.c_double]
    lib.sim_usb_in_buf.restype = ctypes.c_void_p
    lib.sim_usb_out.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.sim_set_signal.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_float] * 5
    return lib


def deep_record(sim, line, ms=200.0):
    sim.sim_init()
    t = 0.0
    dec = StreamDecoder()

    def drain():
        n = sim.sim_usb_out_len()
        ev = dec.feed(bytes(sim.sim_usb_out()[:n]))
        sim.sim_usb_out_clear()
        return ev

    for _ in range(20):
        t += 10.0
        sim.sim_step(ctypes.c_double(t))
    drain()
    buf = line.encode() + b"\0"
    ctypes.memmove(sim.sim_usb_in_buf(), buf, len(buf))
    sim.sim_usb_in_commit()
    events = drain()
    end = t + ms
    while t < end:
        t += 10.0
        sim.sim_step(ctypes.c_double(t))
        events += drain()
    return events, dec


def test_sim_deep_both_channels(sim):
    events, dec = deep_record(sim, "DEEP")
    lines = [e[1] for e in events if e[0] == "line"]
    plan = parse_kv(lines[0])
    assert lines[0].startswith("OK") and plan["N"] == 196608 and plan["RATE"] == 75e6
    assert math.isclose(plan["SECONDS"], 196608 / 75e6, rel_tol=1e-3)
    data = parse_kv([l for l in lines if l.startswith("DEEP DATA")][0])
    assert lines[-1] == "DEEP END" and data["TRIG"] > 0
    assert dec.samples == 196608 and dec.lost_samples == 0 and dec.crc_errors == 0
    payload = b"".join(e[4] for e in events if e[0] == "block")
    ch1, ch2 = payload[0::2], payload[1::2]
    # CH1: the default 3 Vpp 50 kHz sine, 1500 samples per period at 75 MS/s; CH2: a 0..3.3 V square
    assert 65 < max(ch1) - min(ch1) < 90 and 75 < max(ch2) - min(ch2) < 95
    t = int(data["TRIG"])
    assert ch1[t - 1] < 128 <= ch1[t]


def test_sim_deep_one_channel_at_a_lower_rate(sim):
    events, dec = deep_record(sim, "DEEP 0 2 10e6", ms=200.0)
    plan = parse_kv([e[1] for e in events if e[0] == "line"][0])
    assert plan["N"] == 393216 and plan["DIV"] == 8 and plan["RATE"] == pytest.approx(9.375e6)
    assert any(e[0] == "line" and e[1] == "DEEP END" for e in events)    # 42 ms of signal, sent after it
    assert dec.samples == 393216 and dec.crc_errors == 0
