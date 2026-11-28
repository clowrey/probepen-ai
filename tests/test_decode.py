"""Bus decoding: firmware/src/decode.c on frames built here (an encoder written separately from the decoder:
UART, classic CAN, CAN FD with and without bit-rate switching, extended IDs, errors), the DECODE command,
whole decodes through the simulated board (sim/sim.c, its own CAN FD encoder), and the meter's AC RMS and
pulse-width measurements."""
from __future__ import annotations

import ctypes
import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"


class Rec(ctypes.Structure):
    _fields_ = [
        ("proto", ctypes.c_uint8), ("err", ctypes.c_uint8), ("t_sample", ctypes.c_uint64), ("len", ctypes.c_uint8),
        ("data", ctypes.c_uint8 * 64), ("framing_errors", ctypes.c_uint16), ("id", ctypes.c_uint32),
        ("ext", ctypes.c_bool), ("fd", ctypes.c_bool), ("brs", ctypes.c_bool), ("esi", ctypes.c_bool),
        ("rtr", ctypes.c_bool), ("ack", ctypes.c_bool), ("dlc", ctypes.c_uint8),
    ]


EMIT = ctypes.CFUNCTYPE(None, ctypes.POINTER(Rec), ctypes.c_void_p)
DEC_BYTES = 4096   # room for dec_t


def _build(tmp, name, extra=(), defs=()):
    if not shutil.which("gcc"):
        pytest.skip("gcc not installed")
    so = tmp / name
    srcs = [*extra] + [str(s) for s in sorted((FW / "src").glob("*.c")) if s.name != "main.c"]
    subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-D_DEFAULT_SOURCE", *defs, "-shared",
                    "-fPIC", f"-I{FW / 'include'}", *srcs, "-lm", "-o", str(so)], check=True)
    return ctypes.CDLL(str(so))


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    lib = _build(tmp_path_factory.mktemp("decode"), "libcore.so")
    lib.dec_plan.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_float, ctypes.c_float,
                             ctypes.c_float]
    lib.dec_plan.restype = ctypes.c_bool
    lib.dec_push_codes.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, EMIT,
                                   ctypes.c_void_p]
    lib.dec_push_words.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t, EMIT,
                                   ctypes.c_void_p]
    lib.dec_skip.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_bool, EMIT, ctypes.c_void_p]
    lib.dec_flush.argtypes = [ctypes.c_void_p, EMIT, ctypes.c_void_p]
    lib.dec_format_line.argtypes = [ctypes.c_void_p, ctypes.POINTER(Rec), ctypes.c_char_p, ctypes.c_size_t]
    lib.dec_format_text.argtypes = [ctypes.POINTER(Rec), ctypes.c_char_p, ctypes.c_size_t]
    lib.dec_stuff_count_bits.restype = ctypes.c_uint8
    return lib


# ---- an independent encoder ------------------------------------------------------------------------

def crc(bits, width, poly, init):
    reg = init
    top = 1 << (width - 1)
    for b in bits:
        nxt = b ^ (1 if reg & top else 0)
        reg = (reg << 1) & ((1 << width) - 1)
        if nxt:
            reg ^= poly
    return reg


def bits_of(v, n):
    return [(v >> k) & 1 for k in range(n - 1, -1, -1)]


FD_LEN = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]


def can_frame(can_id, data, fd=True, brs=True, ext=False, rtr=False, corrupt=None):
    """Bit list [(level 0 = dominant, phase)] for one frame, phase 'a' arbitration, 'd' data, 'b' BRS bit,
    'c' CRC delimiter (as ISO 11898-1 lays it out; stuffing per the same rules the firmware states)."""
    if fd:
        dlc = next(i for i, n in enumerate(FD_LEN) if n >= len(data))
        data = list(data) + [0] * (FD_LEN[dlc] - len(data))
    else:
        dlc = len(data)
    f = [0]
    if ext:
        f += bits_of(can_id >> 18, 11) + [1, 1] + bits_of(can_id & 0x3FFFF, 18) + [0 if fd else int(rtr)]
        f += [1, 0] if fd else [0, 0]                 # FDF / r1, then res / r0
    else:
        f += bits_of(can_id, 11) + [0 if fd else int(rtr), 0]
        f += [1, 0] if fd else [0]                    # FDF, res / r0
    brs_at = None
    if fd:
        brs_at = len(f)
        f += [int(brs), 0]                            # BRS, ESI
    f += bits_of(dlc, 4)
    for byte in ([] if rtr else data):
        f += bits_of(byte, 8)
    out = []
    phase = "a"
    prev, same, stuff = None, 0, 0
    raw = []                                          # SOF .. data with dynamic stuff bits (FD CRC input)
    for i, b in enumerate(f):
        ph = "b" if (i == brs_at and brs) else phase
        out.append((b, ph))
        raw.append(b)
        if i == brs_at and brs:
            phase = "d"
        same = same + 1 if b == prev else 1
        prev = b
        last = i == len(f) - 1
        if same == 5 and not (fd and last):
            s = 1 - b
            out.append((s, phase))
            raw.append(s)
            stuff += 1
            prev, same = s, 1
    if fd:
        n = len(data)
        sc = (stuff % 8) ^ ((stuff % 8) >> 1)
        sc_bits = bits_of(sc, 3) + [sum(bits_of(sc, 3)) & 1]
        if n <= 16:
            c = crc(raw + sc_bits, 17, 0x1685B, 1 << 16)
            tail = sc_bits + bits_of(c, 17)
        else:
            c = crc(raw + sc_bits, 21, 0x102899, 1 << 20)
            tail = sc_bits + bits_of(c, 21)
        if corrupt == "crc":
            tail[-1] ^= 1
        if corrupt == "sc":
            tail[0] ^= 1
        for k, b in enumerate(tail):
            if k % 4 == 0:
                out.append((1 - prev, phase))
                prev = 1 - prev
            out.append((b, phase))
            prev = b
    else:
        c = crc(f, 15, 0x4599, 0)
        cb = bits_of(c, 15)
        if corrupt == "crc":
            cb[-1] ^= 1
        for b in cb:
            out.append((b, "a"))
            same = same + 1 if b == prev else 1
            prev = b
            if same == 5:
                out.append((1 - b, "a"))
                prev, same = 1 - b, 1
    out.append((1, "c" if (fd and brs) else "a"))     # CRC delimiter
    out.append((0, "a"))                              # ACK
    out += [(1, "a")] * 8                             # ACK delimiter, EOF
    return out


def render(bits, fs, arb, data, idle_bits=20, lo=60, hi=190, noise=1.0, seed=1, t0=0.3):
    """Samples of a bus line: recessive (1) = lo, dominant (0) = hi, as CAN_H or CH1 - CH2 reads."""
    rng = random.Random(seed)
    ta, td = 1.0 / arb, 1.0 / data
    dur = {"a": ta, "d": td, "b": 0.8 * ta + 0.2 * td, "c": 0.8 * td + 0.2 * ta}
    edges, t = [], idle_bits * ta + t0 / fs
    for b, ph in bits:
        edges.append((t, b))
        t += dur[ph]
    end = t + idle_bits * ta
    out, k = [], 0
    level = 1
    for i in range(int(end * fs)):
        ts = i / fs
        while k < len(edges) and edges[k][0] <= ts:
            level = edges[k][1]
            k += 1
        v = (lo if level else hi) + rng.gauss(0, noise)
        out.append(max(0, min(255, int(round(v)))))
    return out


def uart_samples(text, baud, fs, idle_bits=15, lo=40, hi=210, invert=False, noise=1.0, seed=2):
    rng = random.Random(seed)
    bits = [1] * idle_bits
    for ch in text.encode():
        bits += [0] + [(ch >> k) & 1 for k in range(8)] + [1]
    bits += [1] * idle_bits
    n = int(len(bits) / baud * fs)
    out = []
    for i in range(n):
        b = bits[min(len(bits) - 1, int(i / fs * baud))]
        if invert:
            b ^= 1
        out.append(max(0, min(255, int(round((hi if b else lo) + rng.gauss(0, noise))))))
    return out


def run(core, proto, src, rate, data_rate, codes, chunk=777):
    d = ctypes.create_string_buffer(DEC_BYTES)
    assert core.dec_plan(d, proto, src, rate, data_rate, 75e6)
    recs = []
    lines = []

    def cb(p, _):
        r = Rec.from_buffer_copy(p.contents)
        recs.append(r)
        buf = ctypes.create_string_buffer(400)
        core.dec_format_line(d, ctypes.byref(r), buf, 400)
        lines.append(buf.value.decode())

    emit = EMIT(cb)
    arr = (ctypes.c_uint8 * len(codes))(*codes)
    for i in range(0, len(codes), chunk):   # in pieces, as the DMA ring hands them over
        n = min(chunk, len(codes) - i)
        core.dec_push_codes(d, ctypes.cast(ctypes.byref(arr, i), ctypes.POINTER(ctypes.c_uint8)), n, emit, None)
    core.dec_flush(d, emit, None)
    return recs, lines, d


def fs_of(d):
    return ctypes.c_float.from_buffer(d, 16).value   # proto, src (2 bytes) + pad, rate, data_rate, clkdiv, fs


# ---- plan --------------------------------------------------------------------------------------------

def test_plan(core):
    d = ctypes.create_string_buffer(DEC_BYTES)
    assert core.dec_plan(d, 1, 1, 115200, 0, 75e6)
    rate, data_rate = (ctypes.c_float.from_buffer(d, o).value for o in (4, 8))
    clkdiv = ctypes.c_uint32.from_buffer(d, 12).value
    assert data_rate == rate == 115200 and clkdiv == 81                       # 926 kS/s, 8 per bit
    assert core.dec_plan(d, 2, 3, 500e3, 2e6, 75e6) and ctypes.c_uint32.from_buffer(d, 12).value == 4   # 18.75 MS/s
    assert core.dec_plan(d, 2, 3, 1e6, 8e6, 75e6) and ctypes.c_uint32.from_buffer(d, 12).value == 2     # 4.7 per bit
    assert not core.dec_plan(d, 2, 3, 1e6, 20e6, 75e6)                       # under 4 samples per bit
    assert not core.dec_plan(d, 2, 3, 1e6, 0.5e6, 75e6)                      # data slower than arbitration
    assert not core.dec_plan(d, 3, 1, 1e6, 0, 75e6) and not core.dec_plan(d, 1, 4, 9600, 0, 75e6)
    assert core.dec_plan(d, 2, 3, 1e6, 8e6, 125e6) and ctypes.c_uint32.from_buffer(d, 12).value == 3    # overclocked


def test_stuff_count_gray_code(core):
    gray = [0b000, 0b001, 0b011, 0b010, 0b110, 0b111, 0b101, 0b100]
    for n in range(20):
        g = gray[n % 8]
        assert core.dec_stuff_count_bits(n) == (g << 1) | (bin(g).count("1") & 1)


# ---- UART --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("baud,invert", [(9600, False), (115200, False), (1e6, False), (115200, True)])
def test_uart(core, baud, invert):
    d = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(d, 1, 1, baud, 0, 75e6)
    fs = fs_of(d)
    text = "Hello world 0042\r\nsecond line\n" + "x" * 70
    codes = uart_samples(text, baud, fs, idle_bits=25, invert=invert)
    recs, lines, _ = run(core, 1, 1, baud, 0, codes)
    got = b"".join(bytes(r.data[:r.len]) for r in recs)
    assert got == text.encode()
    assert [bytes(r.data[:r.len]) for r in recs][:2] == [b"Hello world 0042\r\n", b"second line\n"]
    assert all(r.framing_errors == 0 for r in recs)
    assert lines[0].startswith("DEC UART T=") and "HEX=48656C6C6F" in lines[0]
    t = float(lines[0].split("T=")[1].split()[0])
    assert t == pytest.approx(25 / baud, abs=2 / baud)                        # the first start bit


def test_uart_framing_error_and_text(core):
    d = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(d, 1, 2, 115200, 0, 75e6)
    fs = fs_of(d)
    codes = uart_samples("AB\n", 115200, fs, idle_bits=25)
    # break the stop bit of 'B': its tenth bit time held low
    spb = fs / 115200
    start = int((25 + 10 + 9) * spb)
    for i in range(start + 2, int(start + spb - 2)):
        codes[i] = 40
    recs, _, _ = run(core, 1, 2, 115200, 0, codes)
    assert recs[0].framing_errors >= 1
    out = ctypes.create_string_buffer(100)
    r = Rec(proto=1, len=5)
    r.data[:5] = list(b"hi\r\n\x01")
    core.dec_format_text(ctypes.byref(r), out, 100)
    assert out.value == b"hi\n."


# ---- CAN ---------------------------------------------------------------------------------------------

CASES = [
    dict(can_id=0x123, data=b"Hello world 0042", fd=True, brs=True, arb=500e3, rate=2e6),
    dict(can_id=0x7FF, data=bytes(range(64)), fd=True, brs=True, arb=1e6, rate=5e6),          # CRC-21
    dict(can_id=0x000, data=b"\x00" * 20, fd=True, brs=True, arb=500e3, rate=2e6),             # many stuff bits
    dict(can_id=0x456, data=b"Hello world 0042", fd=True, brs=False, arb=500e3, rate=500e3),   # FD, no switch
    dict(can_id=0x1ABCDE01, data=b"ext FD frame", fd=True, brs=True, ext=True, arb=500e3, rate=2e6),
    dict(can_id=0x321, data=b"classic!", fd=False, brs=False, arb=500e3, rate=500e3),          # CRC-15
    dict(can_id=0x0, data=b"\xff" * 8, fd=False, brs=False, arb=250e3, rate=250e3),
    dict(can_id=0x12345678 & 0x1FFFFFFF, data=b"ext", fd=False, brs=False, ext=True, arb=125e3, rate=125e3),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['can_id']:x}-{'fd' if c['fd'] else 'cc'}-{len(c['data'])}")
def test_can_frames(core, case):
    d = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(d, 2, 3, case["arb"], case["rate"], 75e6)
    fs = fs_of(d)
    frame = can_frame(case["can_id"], case["data"], fd=case["fd"], brs=case["brs"], ext=case.get("ext", False))
    codes = render(frame * 1, fs, case["arb"], case["rate"]) + render(frame, fs, case["arb"], case["rate"], seed=9)
    recs, lines, _ = run(core, 2, 3, case["arb"], case["rate"], codes)
    assert len(recs) == 2, lines
    for r in recs:
        assert r.err == 0, lines
        assert r.id == case["can_id"] and r.ext == case.get("ext", False) and r.fd == case["fd"]
        assert r.brs == case["brs"] and r.ack
        n = len(case["data"])
        assert bytes(r.data[:n]) == case["data"]
    assert "ERR=NONE" in lines[0] and f"FD={int(case['fd'])}" in lines[0]


def test_can_remote_frame(core):
    d = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(d, 2, 1, 500e3, 0, 75e6)
    fs = fs_of(d)
    bits = can_frame(0x2AA, b"\x00" * 4, fd=False, rtr=True)
    recs, _, _ = run(core, 2, 1, 500e3, 0, render(bits, fs, 500e3, 500e3))
    assert len(recs) == 1 and recs[0].rtr and recs[0].len == 0 and recs[0].dlc == 4 and recs[0].err == 0


@pytest.mark.parametrize("corrupt,err", [("crc", 2), ("sc", 3)])
def test_can_errors(core, corrupt, err):
    d = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(d, 2, 3, 500e3, 2e6, 75e6)
    fs = fs_of(d)
    bad = can_frame(0x123, b"Hello world 0042", corrupt=corrupt)
    good = can_frame(0x124, b"after")
    recs, lines, _ = run(core, 2, 3, 500e3, 2e6, render(bad, fs, 500e3, 2e6) + render(good, fs, 500e3, 2e6))
    assert [r.err for r in recs] == [err, 0], lines
    assert recs[1].id == 0x124
    assert ("ERR=CRC" if err == 2 else "ERR=SC") in lines[0]


def test_can_stuff_error_and_gap(core):
    d = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(d, 2, 3, 500e3, 2e6, 75e6)
    fs = fs_of(d)
    good = can_frame(0x100, b"one")
    bits = can_frame(0x101, b"two")
    bits = bits[:5] + [(0, "a")] * 7 + bits[12:]       # seven dominant bits in the ID: a stuff error
    codes = render(good, fs, 500e3, 2e6) + render(bits, fs, 500e3, 2e6) + render(good, fs, 500e3, 2e6)
    recs, lines, _ = run(core, 2, 3, 500e3, 2e6, codes)
    assert [r.err for r in recs] == [0, 1, 0], lines
    # samples lost mid-frame: that frame is dropped, the next one decodes
    dd = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(dd, 2, 3, 500e3, 2e6, 75e6)
    got = []
    emit = EMIT(lambda p, _: got.append(Rec.from_buffer_copy(p.contents)))
    a = render(good, fs, 500e3, 2e6)
    first = (ctypes.c_uint8 * len(a))(*a)
    core.dec_push_codes(dd, first, len(a) // 2, emit, None)
    core.dec_skip(dd, 5000, True, emit, None)
    rest = (ctypes.c_uint8 * len(a))(*a)
    core.dec_push_codes(dd, rest, len(a), emit, None)
    assert [r.id for r in got] == [0x100]


def test_push_words_matches_codes(core):
    """The live path takes raw PIO words: channel A bit-reversed, both inverted, CH1 - CH2 for differential."""
    d = ctypes.create_string_buffer(DEC_BYTES)
    core.dec_plan(d, 2, 3, 500e3, 2e6, 75e6)
    fs = fs_of(d)
    codes = render(can_frame(0x5A5, b"words"), fs, 500e3, 2e6)

    def rbit8(v):
        return int(f"{v:08b}"[::-1], 2)

    words = []
    for c in codes:   # CAN_H = 128 + diff / 2, CAN_L = 128 - diff / 2
        diff = c - 128
        a, b = 128 + diff // 2, 128 - (diff - diff // 2)
        words.append(rbit8(255 - a) | ((255 - b) << 16) | 0x5A00A500)
    got = []
    emit = EMIT(lambda p, _: got.append(Rec.from_buffer_copy(p.contents)))
    arr = (ctypes.c_uint32 * len(words))(*words)
    core.dec_push_words(d, arr, len(words), emit, None)
    assert len(got) == 1 and got[0].id == 0x5A5 and bytes(got[0].data[:5]) == b"words" and got[0].err == 0


# ---- DECODE command ----------------------------------------------------------------------------------

def test_decode_command(core):
    cmd_t = type("UsbCmd", (ctypes.Structure,), {"_fields_": [
        ("kind", ctypes.c_int), ("arg_i", ctypes.c_int32), ("arg_f", ctypes.c_float), ("valid", ctypes.c_bool),
        ("arg_i2", ctypes.c_int32), ("arg_f2", ctypes.c_float)]})
    core.usb_parse_command.restype = cmd_t
    c = core.usb_parse_command(b"DECODE UART 1 115200")
    assert (c.kind, c.arg_i, c.arg_i2, c.arg_f, c.valid) == (13, 1, 1, 115200.0, True)
    c = core.usb_parse_command(b"decode can 3 500000 2e6")
    assert (c.arg_i, c.arg_i2, c.arg_f, c.arg_f2, c.valid) == (2, 3, 500000.0, 2e6, True)
    assert core.usb_parse_command(b"DECODE 0").valid and core.usb_parse_command(b"DECODE STOP").arg_i == 0
    assert not core.usb_parse_command(b"DECODE UART 4 9600").valid
    assert not core.usb_parse_command(b"DECODE SPI 1 1e6").valid
    app = ctypes.create_string_buffer(8192)
    core.app_init(app)
    reply = ctypes.create_string_buffer(512)
    core.app_usb_command(app, b"DECODE CAN 3 500000 2000000", reply, 512)
    r = reply.value.decode()
    assert r.startswith("OK PROTO=CAN SRC=3") and "MODE=BURST" in r and "FS=18750000.0" in r
    core.app_usb_command(app, b"DECODE UART 1 115200", reply, 512)
    assert "MODE=LIVE" in reply.value.decode()
    core.app_usb_command(app, b"DECODE CAN 1 1e6 40e6", reply, 512)
    assert reply.value == b"ERR"


# ---- whole decodes through the simulated board ------------------------------------------------------

@pytest.fixture(scope="module")
def sims(tmp_path_factory):
    out = {}
    for mhz in (150, 250):
        lib = _build(tmp_path_factory.mktemp(f"sim{mhz}"), "libsim.so", [str(ROOT / "sim" / "sim.c")],
                     [f"-DPROBEPEN_SYS_MHZ={mhz}"])
        lib.sim_step.argtypes = [ctypes.c_double]
        lib.sim_info.restype = ctypes.c_char_p
        lib.sim_usb_in_buf.restype = ctypes.c_void_p
        lib.sim_usb_out.restype = ctypes.POINTER(ctypes.c_uint8)
        lib.sim_set_signal.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_float] * 5
        lib.sim_set_bitrate.argtypes = [ctypes.c_int, ctypes.c_float]
        out[mhz] = lib
    return out


def sim_decode(sim, sigs, rates, command, ms=200.0):
    sim.sim_init()
    for ch, s in enumerate(sigs):
        sim.sim_set_signal(ch, *s)
        sim.sim_set_bitrate(ch, rates[ch])
    t = 0.0

    def cmd(line):
        b = line.encode() + b"\0"
        ctypes.memmove(sim.sim_usb_in_buf(), b, len(b))
        sim.sim_usb_in_commit()

    def drain():
        n = sim.sim_usb_out_len()
        data = bytes(sim.sim_usb_out()[:n])
        sim.sim_usb_out_clear()
        return data

    for _ in range(10):
        t += 10.0
        sim.sim_step(ctypes.c_double(t))
    drain()
    cmd("GAIN 1")
    cmd(command)
    out = b""
    t0 = t
    while t - t0 < ms:
        t += 16.0
        sim.sim_step(ctypes.c_double(t))
        out += drain()
    info = json.loads(sim.sim_info())
    cmd("DECODE 0")
    t += 16.0
    sim.sim_step(ctypes.c_double(t))
    out += drain()
    return [l for l in out.decode().splitlines()], info


def test_sim_uart_live(sims):
    lines, info = sim_decode(sims[150], [(7, 3.3, 0, 115200, 0.5, 0.01), (0, 0, 0, 1, 0.5, 0)], [0, 0],
                             "DECODE UART 1 115200")
    assert any(l.startswith("OK PROTO=UART") and "MODE=LIVE" in l for l in lines)
    recs = [l for l in lines if l.startswith("DEC UART")]
    texts = [bytes.fromhex(l.split("HEX=")[1]).decode() for l in recs[:-1]]
    assert len(texts) > 100 and all(t.startswith("Hello world ") and t.endswith("\r\n") for t in texts)
    nums = [int(t.split()[2]) for t in texts]
    assert all(b == a + 1 for a, b in zip(nums, nums[1:]))                    # every message, in order
    assert info["decoding"] == 1 and info["dec_errors"] == 0 and info["dec_speed"] == pytest.approx(1.0)
    assert lines[-1].startswith("DECODE END") and "ERRORS=0" in lines[-1]


@pytest.mark.parametrize("mhz,arb,data,src,mode", [
    (150, 500e3, 2e6, 3, "BURST"), (150, 500e3, 500e3, 1, "LIVE"), (150, 1e6, 5e6, 2, "BURST"),
    (250, 500e3, 2e6, 3, "BURST"), (250, 1e6, 1e6, 3, "LIVE"),
])
def test_sim_can_fd(sims, mhz, arb, data, src, mode):
    sigs = [(9, 0, 0, arb, 0.5, 0.02), (10, 0, 0, arb, 0.5, 0.02)]
    lines, info = sim_decode(sims[mhz], sigs, [data, data], f"DECODE CAN {src} {arb:.0f} {data:.0f}", ms=400.0)
    ok = next(l for l in lines if l.startswith("OK PROTO=CAN"))
    assert f"MODE={mode}" in ok
    frames = [l for l in lines if l.startswith("DEC CAN")]
    assert len(frames) >= 20 and all("ID=123" in f and "ERR=NONE" in f and "ACK=1" in f for f in frames)
    assert all(bytes.fromhex(f.split("HEX=")[1]).decode().startswith("Hello world ") for f in frames)
    assert all(("BRS=1" in f) == (data > arb) for f in frames)
    if mode == "LIVE":   # one frame per millisecond, none missed
        ts = [float(f.split("T=")[1].split()[0]) for f in frames]
        assert all(b - a == pytest.approx(1e-3, abs=1e-5) for a, b in zip(ts, ts[1:]))
    assert info["sys_mhz"] == mhz and info["adc_max"] == mhz * 1e6 / 2


def test_sim_overclock_rates(sims):
    for mhz, adc in ((150, 75e6), (250, 125e6)):
        sim = sims[mhz]
        sim.sim_init()
        info = json.loads(sim.sim_info())
        assert info["adc_max"] == adc and info["dec_live_max"] == pytest.approx(10e6 * mhz / 150)


# ---- meter: AC RMS and pulse widths ----------------------------------------------------------------

class ScopeMeas(ctypes.Structure):
    _fields_ = [("vmin", ctypes.c_float), ("vmax", ctypes.c_float), ("vpp", ctypes.c_float), ("vavg", ctypes.c_float),
                ("freq_hz", ctypes.c_float), ("trigger_idx", ctypes.c_size_t), ("triggered", ctypes.c_bool),
                ("vrms_ac", ctypes.c_float), ("t_high_s", ctypes.c_float), ("t_low_s", ctypes.c_float),
                ("duty", ctypes.c_float)]


def measure(core, samples, fs):
    core.scope_measure_buffer.argtypes = [ctypes.POINTER(ctypes.c_uint16), ctypes.c_size_t, ctypes.c_float,
                                          ctypes.c_float, ctypes.c_float, ctypes.c_int, ctypes.POINTER(ScopeMeas)]
    m = ScopeMeas()
    arr = (ctypes.c_uint16 * len(samples))(*samples)
    core.scope_measure_buffer(arr, len(samples), fs, 0.04, 128.0, 0, ctypes.byref(m))
    return m


def test_meter_rms_and_pulse_widths(core):
    fs = 1e6
    sine = [int(round(128 + 50 * math.sin(2 * math.pi * 1234.5 * i / fs))) for i in range(20000)]
    m = measure(core, sine, fs)
    assert m.vrms_ac == pytest.approx(50 / math.sqrt(2) * 0.04, rel=0.01)
    pwm = [200 if ((i / fs) * 20e3 + 0.37) % 1.0 < 0.3 else 60 for i in range(4000)]   # 20 kHz, 30 %
    m = measure(core, pwm, fs)
    assert m.t_high_s == pytest.approx(15e-6, abs=0.6e-6) and m.t_low_s == pytest.approx(35e-6, abs=0.6e-6)
    assert m.duty == pytest.approx(0.30, abs=0.01) and m.freq_hz == pytest.approx(20e3, rel=0.01)
    assert m.vrms_ac == pytest.approx(140 * math.sqrt(0.3 * 0.7) * 0.04, rel=0.02)
    flat = measure(core, [128] * 500, fs)
    assert flat.t_high_s == 0 and flat.duty == 0 and flat.vrms_ac == pytest.approx(0, abs=1e-6)


def test_host_parses_decoded_lines():
    import sys
    sys.path.insert(0, str(ROOT / "host"))
    from probepen import parse_decoded
    r = parse_decoded("DEC UART T=0.001736 BYTES=18 FE=0 HEX=48656C6C6F20776F726C6420303030310D0A")
    assert r["proto"] == "UART" and r["t"] == 0.001736 and r["text"] == "Hello world 0001\n" and r["fe"] == "0"
    r = parse_decoded("DEC CAN T=0.002000 ID=123 EXT=0 FD=1 BRS=1 ESI=0 RTR=0 DLC=10 LEN=16 ACK=1 ERR=NONE "
                      "HEX=48656C6C6F20776F726C642030303432")
    assert r["id"] == "123" and r["err"] == "NONE" and r["data"] == b"Hello world 0042"



# ---- DECODE mode on the pen, auto-baud, and what it says when nothing decodes -------------------

class Pen:
    """The simulated pen driven by its buttons (sim_set_buttons), as a hand would."""

    def __init__(self, sim):
        self.sim, self.t = sim, 0.0
        sim.sim_init()

    def step(self, ms):
        end = self.t + ms
        while self.t < end:
            self.t += 16.0
            self.sim.sim_step(ctypes.c_double(self.t))

    def press(self, button):
        self.sim.sim_set_buttons(1 << button)
        self.step(64)
        self.sim.sim_set_buttons(0)
        self.step(48)

    def out(self):
        n = self.sim.sim_usb_out_len()
        data = bytes(self.sim.sim_usb_out()[:n])
        self.sim.sim_usb_out_clear()
        return data.decode(errors="replace").splitlines()

    def info(self):
        return json.loads(self.sim.sim_info())


ASK, MODE, UP, DOWN = 0, 1, 2, 3


def test_decode_mode_on_the_buttons(sims):
    pen = Pen(sims[150])
    pen.sim.sim_set_signal(0, 7, 3.3, 0, 57600, 0.5, 0.01)          # UART at 57600 on CH1
    pen.sim.sim_set_signal(1, 0, 0, 0, 1, 0.5, 0)
    pen.step(200)
    for _ in range(5):                                               # CH1+2 -> ... -> METER -> DECODE
        pen.press(MODE)
    pen.out()
    pen.step(600)
    info = pen.info()
    assert info["mode"] == "DECODE" and info["decoding"] == 1 and info["dec_preset"] == 0
    assert info["dec_auto"] == 0 and info["dec_rate"] == 57600       # auto-baud listened, then picked 57600
    lines = [l for l in pen.out() if l.startswith("DEC UART")]
    assert len(lines) > 100 and all("FE=0" in l for l in lines)
    assert bytes.fromhex(lines[0].split("HEX=")[1]).decode().startswith("Hello world ")
    pen.press(ASK)                                                   # hold
    info = pen.info()
    assert info["mode"] == "DECODE" and info["decoding"] == 0 and info["dec_hold"] == 1
    # CAN FD on the tips, DOWN from the first bus wraps to the last: CAN FD 1M/8M, then DOWN to 1M/5M
    for ch, kind in ((0, 9), (1, 10)):
        pen.sim.sim_set_signal(ch, kind, 0, 0, 1e6, 0.5, 0.02)
        pen.sim.sim_set_bitrate(ch, 5e6)
    pen.press(DOWN)
    pen.press(DOWN)
    pen.out()
    pen.step(400)
    info = pen.info()
    assert (info["dec_rate"], info["dec_data"], info["dec_src"], info["decoding"]) == (1e6, 5e6, 3, 1)
    frames = [l for l in pen.out() if l.startswith("DEC CAN")]
    assert frames and all("ERR=NONE" in f and "ID=123" in f for f in frames)
    pen.press(MODE)                                                  # leave: decoding stops, AI is next
    info = pen.info()
    assert info["mode"] == "AI" and info["decoding"] == 0
    pen.press(MODE)                                                  # and then back to the start
    assert pen.info()["mode"] == "CH1+2"


def test_host_decode_puts_the_pen_in_decode_mode(sims):
    pen = Pen(sims[150])
    pen.sim.sim_set_signal(0, 7, 3.3, 0, 115200, 0.5, 0.01)
    pen.step(100)
    b = b"DECODE UART 1 115200\0"
    ctypes.memmove(pen.sim.sim_usb_in_buf(), b, len(b))
    pen.sim.sim_usb_in_commit()
    pen.step(100)
    info = pen.info()
    assert info["mode"] == "DECODE" and info["dec_preset"] == -1 and info["decoding"] == 1
    pen.press(UP)                                                    # UP: the pen's own buses, from the first
    assert pen.info()["dec_preset"] == 0


def test_wrong_rate_shows_the_rate_the_edges_have(sims):
    sig = [(7, 3.3, 0, 115200, 0.5, 0.01), (0, 0, 0, 1, 0.5, 0)]
    _, info = sim_decode(sims[150], sig, [0, 0], "DECODE UART 1 460800")         # set too fast: garbage ...
    assert info["dec_check"] == 2 and info["dec_seen"] == pytest.approx(115200, rel=0.05)    # ... and why
    _, info = sim_decode(sims[150], sig, [0, 0], "DECODE UART 1 9600")           # set too slow
    assert info["dec_check"] == 1                                    # edges too fast for 77 kS/s to time
    _, info = sim_decode(sims[150], sig, [0, 0], "DECODE UART 1 115200")         # right
    assert info["dec_check"] == 0 and info["dec_errors"] == 0


def test_differential_decode_puts_both_channels_on_one_range(core):
    app = ctypes.create_string_buffer(8192)
    core.app_init(app)                                               # both channels start on +-26 V
    reply = ctypes.create_string_buffer(512)
    core.app_usb_command(app, b"GAIN 0", reply, 512)
    n = 2000
    lo = (ctypes.c_uint16 * n)(*([128 + 10] * (n // 2) + [128 - 10] * (n // 2)))
    core.app_process_scope(app, lo, lo, n)                           # small signals: auto-range goes to +-5 V ...
    core.app_usb_command(app, b"DECODE CAN 3 500000 2000000", reply, 512)
    assert reply.value.startswith(b"OK")
    core.app_format_meas.restype = ctypes.c_size_t
    meas = ctypes.create_string_buffer(512)
    core.app_format_meas(app, meas, 512)
    assert meas.value.count(b"RANGE=1X") == 2                        # ... and both stay on the same one



# ---- METER: averaging, its own window, two pages ----------------------------------------------------

class MeterAvg(ctypes.Structure):
    _fields_ = [(k, ctypes.c_float) for k in ("dc", "vrms", "vpp", "freq", "t_high", "t_low", "duty")]


def meter_app(core):
    app = ctypes.create_string_buffer(8192)
    core.app_init(app)
    core.app_meter_avg.restype = ctypes.POINTER(MeterAvg)
    core.app_sample_rate_hz.restype = ctypes.c_float
    core.app_timebase_ns.restype = ctypes.c_uint32
    core.app_capture_samples.restype = ctypes.c_size_t
    reply = ctypes.create_string_buffer(512)

    def cmd(line):
        core.app_usb_command(app, line.encode(), reply, 512)
        return reply.value.decode()

    def capture(freq, amp=50.0, seed=0):
        """One capture of a sine at freq (codes), at the rate and length the app asks for now."""
        rng = random.Random(seed)
        fs = core.app_sample_rate_hz(app)
        n = core.app_capture_samples(app, ctypes.c_size_t(4096))
        ph = rng.random()
        a = [int(round(128 + amp * math.sin(2 * math.pi * (freq * i / fs + ph)) + rng.gauss(0, 1.5))) for i in range(n)]
        arr = (ctypes.c_uint16 * n)(*a)
        core.app_process_scope(app, arr, arr, ctypes.c_size_t(n))
        return n / fs

    return app, cmd, capture


def test_meter_picks_its_window_and_gives_the_timebase_back(core):
    app, cmd, capture = meter_app(core)
    cmd("GAIN 1")
    cmd("TB 3")
    scope_tb = core.app_timebase_ns(app)
    cmd("MODE 4")
    for k in range(30):                                  # a 1 kHz sine: the window grows to hold 3 to 40 periods
        window = capture(1e3, seed=k)
    assert 3 <= 1e3 * window <= 40
    assert core.app_meter_avg(app, 0).contents.freq == pytest.approx(1e3, rel=0.01)
    for k in range(30):                                  # then 200 kHz: it shrinks again
        window = capture(200e3, seed=k)
    assert 3 <= 200e3 * window <= 40
    cmd("MODE 0")
    assert core.app_timebase_ns(app) == scope_tb        # the scope's timebase is back


def test_meter_averages_and_follows_real_changes(core):
    app, cmd, capture = meter_app(core)
    cmd("GAIN 1")
    cmd("MODE 4")
    raw = []
    for k in range(60):                                  # amplitude wobbling +-4 % around 50 codes
        capture(5e3, amp=50.0 * (1.04 if k % 2 else 0.96), seed=k)
        raw.append(core.app_meter_avg(app, 0).contents.vrms)
    late = raw[-20:]
    assert max(late) - min(late) < 0.25 * (0.08 * 50 / math.sqrt(2) * 0.0392)   # far steadier than the input
    before = raw[-1]
    capture(5e3, amp=100.0, seed=99)                      # doubled: one odd reading is not believed ...
    assert core.app_meter_avg(app, 0).contents.vrms == pytest.approx(before, rel=0.02)
    capture(5e3, amp=100.0, seed=98)                      # ... a second one is, at once, not crept up to
    assert core.app_meter_avg(app, 0).contents.vrms == pytest.approx(2 * before, rel=0.06)
    # DC from the DC-sense ADC is averaged too
    core.app_set_dc(app, 1163, 1795, 3427)
    first = core.app_meter_avg(app, 0).contents.dc
    for raw_code in (1164, 1162, 1163, 1165, 1161) * 4:
        core.app_set_dc(app, raw_code, 1795, 3427)
    assert core.app_meter_avg(app, 0).contents.dc == pytest.approx(first, abs=0.01)


def test_meter_pages_draw(core):
    app, cmd, capture = meter_app(core)
    cmd("MODE 4")
    core.app_set_dc(app, 1163, 1795, 3427)
    capture(10e3)
    fb_a = (ctypes.c_uint16 * (240 * 135))()
    fb_b = (ctypes.c_uint16 * (240 * 135))()
    core.app_render(app, fb_a, None, None, ctypes.c_size_t(0), None, ctypes.c_size_t(0))
    core.app_button_event(app, 2, 1)                     # UP: the TIMING page
    core.app_render(app, fb_b, None, None, ctypes.c_size_t(0), None, ctypes.c_size_t(0))
    assert len(set(fb_a)) > 3 and len(set(fb_b)) > 3 and list(fb_a) != list(fb_b)
