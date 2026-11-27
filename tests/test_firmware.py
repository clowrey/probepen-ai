"""Stage 6 firmware tests: GPIO parity, PIO verification, native C11 unit tests via ctypes, and ARM Cortex-M33 cross-compilation."""
from __future__ import annotations

import ctypes
import math
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"

import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hw import design  # noqa: E402


class ScopeMeas(ctypes.Structure):
    _fields_ = [
        ("vmin", ctypes.c_float),
        ("vmax", ctypes.c_float),
        ("vpp", ctypes.c_float),
        ("vavg", ctypes.c_float),
        ("freq_hz", ctypes.c_float),
        ("trigger_idx", ctypes.c_size_t),
        ("triggered", ctypes.c_bool),
    ]


class UsbCmd(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_int),
        ("arg_i", ctypes.c_int32),
        ("arg_f", ctypes.c_float),
        ("valid", ctypes.c_bool),
    ]


@pytest.fixture(scope="module")
def core_lib(tmp_path_factory):
    out_so = tmp_path_factory.mktemp("fw_native") / "libprobepen_core.so"
    srcs = sorted((FW / "src").glob("*.c"))
    srcs = [s for s in srcs if s.name != "main.c"]
    cmd = [
        "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
        "-shared", "-fPIC", f"-I{FW / 'include'}",
        *[str(s) for s in srcs], "-lm", "-o", str(out_so),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr

    lib = ctypes.CDLL(str(out_so))
    lib.afe_code_to_volts.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint16]
    lib.afe_code_to_volts.restype = ctypes.c_float
    lib.afe_volts_to_code.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_float]
    lib.afe_volts_to_code.restype = ctypes.c_uint16
    lib.afe_calibrate_two_point.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_uint16, ctypes.c_float, ctypes.c_uint16, ctypes.c_float,
    ]
    lib.afe_calibrate_two_point.restype = ctypes.c_bool
    lib.afe_autorange_select.argtypes = [ctypes.c_int, ctypes.c_float, ctypes.c_bool]
    lib.afe_autorange_select.restype = ctypes.c_int

    lib.scope_unpack_ad9288.argtypes = [
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_uint16), ctypes.c_size_t,
    ]
    lib.scope_unpack_ad9288.restype = ctypes.c_size_t
    lib.scope_measure_buffer.argtypes = [
        ctypes.POINTER(ctypes.c_uint16), ctypes.c_size_t, ctypes.c_float, ctypes.c_float,
        ctypes.c_float, ctypes.c_int, ctypes.POINTER(ScopeMeas),
    ]
    lib.scope_decimate_min_max.argtypes = [
        ctypes.POINTER(ctypes.c_uint16), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t, ctypes.c_uint8,
    ]

    lib.logic_thr_mv_to_pwm_level.argtypes = [ctypes.c_uint32, ctypes.c_uint16, ctypes.c_float]
    lib.logic_thr_mv_to_pwm_level.restype = ctypes.c_uint16
    lib.logic_family_default_mv.argtypes = [ctypes.c_int]
    lib.logic_family_default_mv.restype = ctypes.c_uint32
    lib.logic_classify_sample.argtypes = [ctypes.c_bool, ctypes.c_bool, ctypes.c_uint32]
    lib.logic_classify_sample.restype = ctypes.c_int
    lib.logic_unpack_pod_nibbles.argtypes = [
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ]
    lib.logic_unpack_pod_nibbles.restype = ctypes.c_size_t
    lib.logic_count_pod_edges.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_uint8]
    lib.logic_count_pod_edges.restype = ctypes.c_uint32

    lib.lcd_rgb565.argtypes = [ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
    lib.lcd_rgb565.restype = ctypes.c_uint16
    lib.lcd_st7735_init_seq.argtypes = [ctypes.POINTER(ctypes.c_size_t)]
    lib.lcd_st7735_init_seq.restype = ctypes.c_void_p

    lib.usb_parse_command.argtypes = [ctypes.c_char_p]
    lib.usb_parse_command.restype = UsbCmd
    lib.usb_encode_wave_frame.argtypes = [
        ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint16), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ]
    lib.usb_encode_wave_frame.restype = ctypes.c_size_t
    lib.usb_decode_wave_frame.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint16),
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.usb_decode_wave_frame.restype = ctypes.c_bool
    return lib


def test_gpio_header_matches_design_py_1_to_1():
    pins_h = (FW / "include" / "probepen_pins.h").read_text(encoding="utf8")
    parsed = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"#define\s+PIN_([A-Z0-9_]+)\s+(\d+)u", pins_h)
    }
    assert parsed == design.GPIO


def test_pio_programs_syntax_and_pin_widths():
    adc = (FW / "ad9288_capture.pio").read_text(encoding="utf8")
    pod = (FW / "pod_capture.pio").read_text(encoding="utf8")
    assert ".program ad9288_capture" in adc
    assert ".side_set 1" in adc
    assert "in pins, 32" in adc
    # the PIO window GPIO16..47 must hold both 8-bit buses and the clock pin
    a = [design.GPIO[f"ADCA_D{i}"] for i in range(8)]
    b = [design.GPIO[f"ADCB_D{i}"] for i in range(8)]
    assert sorted(a) == list(range(16, 24)) and a == [23 - i for i in range(8)]
    assert b == list(range(32, 40))
    assert design.GPIO["ADC_CLK"] == 24
    assert ".program pod_capture" in pod
    assert "in pins, 4" in pod


def test_afe_calibration_and_autorange(core_lib):
    cal_buf = (ctypes.c_float * 7)()
    core_lib.afe_cal_init_default(cal_buf)
    assert abs(core_lib.afe_code_to_volts(cal_buf, 0, 128) - 0.0) < 1e-4

    # Simulate 2-point factory calibration compensating a 1% gain error + 40 mV offset
    assert core_lib.afe_calibrate_two_point(cal_buf, 0, 28, -1.96, 228, 1.98)
    v_mid = core_lib.afe_code_to_volts(cal_buf, 0, 128)
    assert abs(v_mid - 0.01) < 1e-3
    code_rt = core_lib.afe_volts_to_code(cal_buf, 0, 1.25)
    assert abs(core_lib.afe_code_to_volts(cal_buf, 0, code_rt) - 1.25) < 0.01

    assert core_lib.afe_autorange_select(0, 4.8, False) == 1
    assert core_lib.afe_autorange_select(0, 4.0, False) == 0
    assert core_lib.afe_autorange_select(0, 0.5, True) == 1
    assert core_lib.afe_autorange_select(1, 4.0, False) == 1      # hysteresis
    assert core_lib.afe_autorange_select(1, 3.0, False) == 0


def test_afe_scale_matches_front_end_resistors(core_lib):
    """Full scale from the divider and gain-stage resistor values in hw/design.py (as in test_design)."""
    r_th = 100.0 * 51.0 / 151.0
    div = r_th / (940.0 + r_th)
    g_hi = 1500.0 / 510.0
    g_lo = (1500.0 * 336.0 / 1836.0) / 510.0
    cal = (ctypes.c_float * 7)()
    core_lib.afe_cal_init_default(cal)
    fs_hi = core_lib.afe_code_to_volts(cal, 0, 255) - core_lib.afe_code_to_volts(cal, 0, 128)
    fs_lo = core_lib.afe_code_to_volts(cal, 1, 255) - core_lib.afe_code_to_volts(cal, 1, 128)
    assert abs(fs_hi - 127 / 256 * 1.024 / (g_hi * div)) < 0.01
    assert abs(fs_lo - 127 / 256 * 1.024 / (g_lo * div)) < 0.05
    assert 4.9 < 0.512 / (g_hi * div) < 5.1

    # DC sense reads the inverting gain stage's output: below REF_MID means a positive tip
    core_lib.afe_dc_sense_to_volts.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint16]
    core_lib.afe_dc_sense_to_volts.restype = ctypes.c_float
    v_pin = 0.978 - 1.0 * g_hi * div
    raw = round(v_pin / 3.0 * 4096)
    assert abs(core_lib.afe_dc_sense_to_volts(cal, 0, raw) - 1.0) < 0.01

    codes = (ctypes.c_uint16 * 3)(0, 128, 255)
    core_lib.scope_invert_codes(codes, 3)
    assert list(codes) == [255, 127, 0]


def test_scope_capture_unpack_measure_and_decimate(core_lib):
    def rbit8(v):
        return int(f"{v:08b}"[::-1], 2)

    # one PIO word per sample: channel A bit-reversed in bits 0..7, channel B in bits 16..23
    a_codes, b_codes = [0x01, 0x80, 0x5A], [0x00, 0xFF, 0x3C]
    w = (ctypes.c_uint32 * 3)(*((bv << 16) | rbit8(av) | (1 << 8) for av, bv in zip(a_codes, b_codes)))
    out_a = (ctypes.c_uint16 * 3)()
    out_b = (ctypes.c_uint16 * 3)()
    assert core_lib.scope_unpack_ad9288(w, 3, out_a, out_b, 3) == 3
    assert list(out_a) == a_codes and list(out_b) == b_codes

    # Synthetic 1 MHz sine sampled at 40 MSPS (1024 samples, 8-bit codes)
    fs = 40_000_000.0
    n = 1024
    samp = (ctypes.c_uint16 * n)(
        *(int(round(128 + 75 * math.sin(2.0 * math.pi * 1_000_000.0 * i / fs))) for i in range(n))
    )
    meas = ScopeMeas()
    core_lib.scope_measure_buffer(samp, n, ctypes.c_float(fs), ctypes.c_float(5.0 / 256.0), ctypes.c_float(128.0), 0, ctypes.byref(meas))
    assert meas.triggered is True
    assert 2.7 < meas.vpp < 3.1
    assert 960_000.0 < meas.freq_hz < 1_040_000.0

    ymin = (ctypes.c_uint8 * 160)()
    ymax = (ctypes.c_uint8 * 160)()
    core_lib.scope_decimate_min_max(samp, n, ymin, ymax, 160, 60)
    assert all(ymin[i] <= ymax[i] < 60 for i in range(160))


def test_logic_probe_and_lcd_and_usb(core_lib):
    assert core_lib.logic_family_default_mv(0) == 1400
    assert core_lib.logic_family_default_mv(1) == 1650
    assert core_lib.logic_family_default_mv(2) == 900
    assert core_lib.logic_thr_mv_to_pwm_level(1650, 1000, 3300.0) == 500
    assert core_lib.logic_classify_sample(True, False, 0) == 1
    assert core_lib.logic_classify_sample(False, True, 0) == 2
    assert core_lib.logic_classify_sample(False, False, 3) == 3

    pod_word = (ctypes.c_uint32 * 1)(0x01010101)
    nibs = (ctypes.c_uint8 * 8)()
    assert core_lib.logic_unpack_pod_nibbles(pod_word, 1, nibs, 8) == 8
    assert core_lib.logic_count_pod_edges(nibs, 8, 0) == 7

    assert core_lib.lcd_rgb565(255, 0, 0) == 0xF800
    assert core_lib.lcd_rgb565(0, 255, 0) == 0x07E0
    assert core_lib.lcd_rgb565(0, 0, 255) == 0x001F
    seq_len = ctypes.c_size_t(0)
    assert core_lib.lcd_st7735_init_seq(ctypes.byref(seq_len)) is not None
    assert seq_len.value >= 6

    fb = (ctypes.c_uint16 * (240 * 135))()
    core_lib.lcd_fb_clear(fb, 0x0000)
    core_lib.lcd_fb_draw_text(fb, 2, 2, b"10V", 0xFFFF, 0x0000)
    assert any(px == 0xFFFF for px in fb)

    c1 = core_lib.usb_parse_command(b"THR 1400")
    assert c1.kind == 4 and c1.valid and abs(c1.arg_f - 1400.0) < 1e-3
    assert core_lib.usb_parse_command(b"GAIN 5").valid is False

    in_s = (ctypes.c_uint16 * 4)(12, 345, 678, 1020)
    frame = (ctypes.c_uint8 * 64)()
    flen = core_lib.usb_encode_wave_frame(7, in_s, 4, frame, 64)
    assert flen == 15
    seq = ctypes.c_uint8(0)
    out_s = (ctypes.c_uint16 * 8)()
    cnt = ctypes.c_size_t(0)
    assert core_lib.usb_decode_wave_frame(frame, flen, ctypes.byref(seq), out_s, 8, ctypes.byref(cnt))
    assert seq.value == 7 and cnt.value == 4 and list(out_s)[:4] == [12, 345, 678, 1020]
    frame[7] ^= 0xFF  # Corrupt payload -> CRC failure
    assert not core_lib.usb_decode_wave_frame(frame, flen, ctypes.byref(seq), out_s, 8, ctypes.byref(cnt))


LCD_W, LCD_H = 240, 135   # default panel: 1.14" ST7789


def _app(core_lib):
    app = ctypes.create_string_buffer(4096)
    core_lib.app_init(app)
    return app


def _cmd(core_lib, app, line: str) -> str:
    reply = ctypes.create_string_buffer(512)
    core_lib.app_usb_command(app, line.encode(), reply, 512)
    return reply.value.decode()


def test_buttons_debounce_short_long_repeat(core_lib):
    b = ctypes.create_string_buffer(256)
    core_lib.buttons_init(b)
    ev = (ctypes.c_uint8 * 4)()

    def step(mask, t):
        n = core_lib.buttons_update(b, mask, t, ev, 4)
        return list(ev)[:n]

    assert step(0b0001, 0) == [] and step(0b0001, 25) == []        # ASK pressed, debounced
    assert step(0b0000, 100) == [] and step(0b0000, 125) == [0x01]  # released: SHORT on button 0
    assert step(0b0100, 200) == [] and step(0b0000, 210) == [] and step(0b0000, 260) == []  # 10 ms bounce
    step(0b0010, 300)
    step(0b0010, 325)
    assert step(0b0010, 925) == [0x12]                             # MODE held 600 ms: LONG
    assert step(0b0010, 1075) == [0x13]                            # then REPEAT every 150 ms
    step(0b0000, 1100)
    assert step(0b0000, 1130) == []                                # no SHORT after a long press


def test_app_usb_commands_and_timebases(core_lib):
    app = _app(core_lib)
    assert _cmd(core_lib, app, "*IDN?").startswith("ProbePen-AI,RP2354B+AD9288,")
    assert _cmd(core_lib, app, "MODE 9") == "ERR"
    assert _cmd(core_lib, app, "MODE 3") == "OK"
    assert _cmd(core_lib, app, "TB 99") == "ERR"
    assert _cmd(core_lib, app, "TB 3") == "OK 1000"
    assert _cmd(core_lib, app, "GAIN 5") == "ERR"
    assert _cmd(core_lib, app, "GAIN 0") == "OK"
    assert _cmd(core_lib, app, "RUN 0") == "OK"
    assert _cmd(core_lib, app, "CAPTURE 300") == "OK 300"
    assert _cmd(core_lib, app, "bogus") == "ERR"

    core_lib.app_timebase_count.restype = ctypes.c_size_t
    core_lib.app_timebase_ns.restype = ctypes.c_uint32
    core_lib.app_clkdiv.restype = ctypes.c_uint32
    core_lib.app_sample_rate_hz.restype = ctypes.c_float
    core_lib.app_screen_samples.restype = ctypes.c_size_t
    for tb in range(core_lib.app_timebase_count()):
        assert _cmd(core_lib, app, f"TB {tb}").startswith("OK")
        k = core_lib.app_clkdiv(app)
        fs = core_lib.app_sample_rate_hz(app)
        assert 1 <= k <= 65535 and fs <= 75e6 + 1
        span = core_lib.app_screen_samples(app)
        tdiv = core_lib.app_timebase_ns(app) * 1e-9
        assert abs(span - fs * 10 * tdiv) <= 1.0, (tb, span, fs, tdiv)
        assert span <= 4 * LCD_W


def test_app_scope_logic_render(core_lib):
    app = _app(core_lib)
    _cmd(core_lib, app, "GAIN 1")
    _cmd(core_lib, app, "TB 6")                      # 10 us/div -> 5 MS/s
    n, fs = 2000, 5e6
    a = (ctypes.c_uint16 * n)(*(int(128 + 50 * math.sin(2 * math.pi * 50e3 * i / fs)) for i in range(n)))
    b = (ctypes.c_uint16 * n)(*(128 + (60 if (i // 100) % 2 else -60) for i in range(n)))
    core_lib.app_process_scope(app, a, b, ctypes.c_size_t(n))
    meas = _cmd(core_lib, app, "MEAS?")
    f1 = float(meas.split("CH1 ")[1].split("F=")[1].split()[0])
    assert 49e3 < f1 < 51e3, meas
    assert "TRIG=YES" in meas

    fb = (ctypes.c_uint16 * (LCD_W * LCD_H))()
    for mode in range(5):
        _cmd(core_lib, app, f"MODE {mode}")
        core_lib.app_render(app, fb, a, b, ctypes.c_size_t(n), None, ctypes.c_size_t(0))
        assert len(set(fb)) > 3, f"mode {mode} drew nothing"
    _cmd(core_lib, app, "MODE 0")
    core_lib.app_render(app, fb, a, b, ctypes.c_size_t(n), None, ctypes.c_size_t(0))
    yellow = core_lib.lcd_rgb565(255, 214, 0)
    assert sum(px == yellow for px in fb) > LCD_W, "CH1 trace missing"

    nib = (ctypes.c_uint8 * n)(*(((i // 25) % 2) for i in range(n)))   # P0: 100 kHz at 5 MS/s
    core_lib.app_process_logic(app, nib, ctypes.c_size_t(n))
    eng = ctypes.create_string_buffer(32)
    core_lib.app_format_eng.argtypes = [ctypes.c_float, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    core_lib.app_format_eng(ctypes.c_float(0.0123), b"V", eng, 32)
    assert eng.value == b"12.3mV"
    core_lib.app_format_eng(ctypes.c_float(1.5e-6), b"s", eng, 32)
    assert eng.value == b"1.50\x7fs"


def test_host_client_reads_firmware_frames(core_lib):
    sys.path.insert(0, str(ROOT / "host"))
    import probepen

    def frame(seq, samples):
        s = (ctypes.c_uint16 * len(samples))(*samples)
        buf = (ctypes.c_uint8 * (7 + 2 * len(samples)))()
        n = core_lib.usb_encode_wave_frame(seq, s, len(samples), buf, len(buf))
        return bytes(buf[:n])

    assert probepen.decode_frame(frame(1, [0, 128, 255])) == (1, [0, 128, 255])

    class FakePort:
        def __init__(self, data):
            self.data, self.sent = data, b""

        def write(self, b):
            self.sent += b

        def read(self, n):
            out, self.data = self.data[:n], self.data[n:]
            return out

    port = FakePort(b"OK 3\n" + frame(0, [10, 20, 30]) + frame(1, [40, 50, 60]))
    pen = probepen.ProbePen(port)
    assert pen.capture(3) == ([10, 20, 30], [40, 50, 60])
    assert port.sent == b"CAPTURE 3\n"

    app = _app(core_lib)
    m = probepen.parse_meas(_cmd(core_lib, app, "MEAS?"))
    assert set(m["CH1"]) >= {"VPP", "VMIN", "VMAX", "F", "RANGE"} and m["TRIG"] == "NO"


def test_peak_detect(core_lib):
    def rbit8(v):
        return int(f"{v:08b}"[::-1], 2)

    # raw PIO words for codes (as the ADC outputs them, before inversion); a 1-sample spike in the middle
    raw_a = [100] * 10 + [250] + [100] * 9
    raw_b = [30] * 5 + [200] * 15
    ring = (ctypes.c_uint32 * 8)()
    words = [rbit8(a) | (b << 16) for a, b in zip(raw_a, raw_b)]
    big = (ctypes.c_uint32 * 20)(*words)
    out_a = (ctypes.c_uint16 * 2)()
    out_b = (ctypes.c_uint16 * 2)()
    core_lib.scope_peak_group(big, 20, 0, 20, out_a, out_b)
    # inverted: 255-100=155 first, then the spike 255-250=5 (min after max in time)
    assert list(out_a) == [155, 5] and list(out_b) == [225, 55]
    for i, w in enumerate(words[:8]):        # wrap-around read from a short ring
        ring[(i + 5) % 8] = w
    core_lib.scope_peak_group(ring, 8, 5, 8, out_a, out_b)
    assert sorted(out_a) == [155, 155] and sorted(out_b) == [55, 225]

    app = _app(core_lib)
    core_lib.app_peak_factor.restype = ctypes.c_uint32
    core_lib.app_clkdiv.restype = ctypes.c_uint32
    core_lib.app_sample_rate_hz.restype = ctypes.c_float
    core_lib.app_timebase_count.restype = ctypes.c_size_t
    seen = 0
    for tb in range(core_lib.app_timebase_count()):
        _cmd(core_lib, app, f"TB {tb}")
        m = core_lib.app_peak_factor(app)
        if m:
            seen += 1
            k = core_lib.app_clkdiv(app)
            assert k % 30 == 0 and m == 2 * k // 30 and m >= 4
            # two outputs per group of m raw samples at 75 MHz / 30 gives the timebase's sample rate
            assert abs(2 * (75e6 / 30) / m - core_lib.app_sample_rate_hz(app)) < 1e-3 * core_lib.app_sample_rate_hz(app)
    assert seen >= 6

    # a signal much faster than a slot: every pair spans the swing, so no frequency is claimed
    _cmd(core_lib, app, "GAIN 1")
    _cmd(core_lib, app, "TB 12")                 # 1 ms/div: peak detect
    assert core_lib.app_peak_factor(app) > 0
    n = 1000
    env = (ctypes.c_uint16 * n)(*([60, 200] * (n // 2)))
    core_lib.app_process_scope(app, env, env, ctypes.c_size_t(n))
    meas = _cmd(core_lib, app, "MEAS?")
    assert "F=0.0" in meas.split(";")[0], meas
    slow = (ctypes.c_uint16 * n)(*(60 if (i // 100) % 2 else 200 for i in range(n)))
    core_lib.app_process_scope(app, slow, slow, ctypes.c_size_t(n))
    assert "F=0.0 " not in _cmd(core_lib, app, "MEAS?").split(";")[0]
    _cmd(core_lib, app, "MODE 3")
    assert core_lib.app_peak_factor(app) == 0    # logic mode samples directly


def test_calibration_record_round_trip(core_lib):
    cal = (ctypes.c_float * 7)()
    core_lib.afe_cal_init_default(cal)
    cal[4] = 0.031                               # a zero offset from CAL
    page = (ctypes.c_uint8 * 256)()
    core_lib.calstore_pack.restype = ctypes.c_size_t
    assert core_lib.calstore_pack(cal, page) == 8 + 28 + 2
    back = (ctypes.c_float * 7)()
    assert core_lib.calstore_unpack(page, back)
    assert list(back) == list(cal)
    erased = (ctypes.c_uint8 * 256)(*([0xFF] * 256))
    assert not core_lib.calstore_unpack(erased, back)    # blank flash: keep the defaults
    page[12] ^= 0x01
    assert not core_lib.calstore_unpack(page, back)      # CRC catches a flipped bit
    cal[3] = 100.0                                       # gain far from the design: refused
    core_lib.calstore_pack(cal, page)
    assert not core_lib.calstore_unpack(page, back)

    app = _app(core_lib)
    _cmd(core_lib, app, "CAL")
    n = 600
    a = (ctypes.c_uint16 * n)(*([131] * n))             # shorted inputs reading 3 codes high
    core_lib.app_process_scope(app, a, a, ctypes.c_size_t(n))
    assert "VAVG=0.000" in _cmd(core_lib, app, "MEAS?").split(";")[0]
    for gain in ("GAIN 1", "GAIN 10"):                   # both ranges zeroed by one CAL
        _cmd(core_lib, app, gain)
        core_lib.app_process_scope(app, a, a, ctypes.c_size_t(n))
        assert "VAVG=0.000" in _cmd(core_lib, app, "MEAS?").split(";")[0], gain


def test_lcd_panel_bytes(core_lib):
    log = []
    CMD = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint8)
    DATA = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t)
    DLY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32)

    class Bus(ctypes.Structure):
        _fields_ = [("write_cmd", CMD), ("write_data", DATA), ("delay_ms", DLY), ("ctx", ctypes.c_void_p)]

    cb_cmd = CMD(lambda ctx, c: log.append(("C", c)))
    cb_data = DATA(lambda ctx, d, n: log.append(("D", bytes(d[:n]))))
    cb_dly = DLY(lambda ctx, ms: None)
    bus = Bus(cb_cmd, cb_data, cb_dly, None)
    core_lib.lcd_panel_init(ctypes.byref(bus))
    cmds = [v for k, v in log if k == "C"]
    assert cmds[0] == 0x01 and 0x11 in cmds and cmds[-1] == 0x29 and 0x3A in cmds
    log.clear()
    core_lib.lcd_panel_set_window(ctypes.byref(bus), 0, 0, LCD_W - 1, LCD_H - 1)
    be = lambda a, b: a.to_bytes(2, "big") + b.to_bytes(2, "big")  # noqa: E731
    assert log == [("C", 0x2A), ("D", be(40, 40 + LCD_W - 1)),
                   ("C", 0x2B), ("D", be(53, 53 + LCD_H - 1)), ("C", 0x2C)]


def _pio_header(pio: Path, out_dir: Path) -> None:
    """Stand-in for pico_generate_pio_header: program object, default config, and the .pio's c-sdk block."""
    text = pio.read_text(encoding="utf8")
    name = re.search(r"^\.program\s+(\w+)", text, re.M).group(1)
    sdk = re.search(r"% c-sdk \{(.*?)%\}", text, re.S).group(1)
    n_instr = len([ln for ln in text.split("% c-sdk")[0].splitlines()
                   if ln.strip() and not ln.strip().startswith((";", ".", "%"))])
    (out_dir / f"{pio.name}.h").write_text(
        '#pragma once\n#include "hardware/pio.h"\n'
        f"static const uint16_t {name}_program_instructions[{n_instr}] = {{0}};\n"
        f"static const pio_program_t {name}_program = {{{name}_program_instructions, {n_instr}, -1, 0}};\n"
        f"static inline pio_sm_config {name}_program_get_default_config(uint offset) {{\n"
        f"    pio_sm_config c = pio_get_default_sm_config();\n"
        f"    sm_config_set_wrap(&c, offset, offset + {n_instr - 1});\n    return c;\n}}\n" + sdk,
        encoding="utf8",
    )


def test_hardware_path_type_checks_against_sdk_signatures(tmp_path):
    """main.c's PICO_RP2350 path and the PIO init code, against the SDK 2.x signatures in tests/fw_stubs."""
    for pio in FW.glob("*.pio"):
        _pio_header(pio, tmp_path)
    res = subprocess.run(
        [
            "arm-none-eabi-gcc", "-std=c11", "-mcpu=cortex-m33", "-mthumb", "-O2", "-Wall", "-Wextra", "-Werror",
            "-DPICO_RP2350", "-include", str(FW / "boards" / "probepen_ai.h"),
            f"-I{FW / 'include'}", f"-I{ROOT / 'tests' / 'fw_stubs'}", f"-I{tmp_path}",
            "-fsyntax-only", str(FW / "src" / "main.c"),
        ],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr


def test_arm_cortex_m33_cross_compile_all_sources(tmp_path):
    for src in sorted((FW / "src").glob("*.c")):
        obj = tmp_path / f"{src.stem}.o"
        res = subprocess.run(
            [
                "arm-none-eabi-gcc", "-std=c11", "-mcpu=cortex-m33", "-mthumb",
                "-O2", "-Wall", "-Wextra", "-Werror",
                f"-I{FW / 'include'}", "-c", str(src), "-o", str(obj),
            ],
            capture_output=True, text=True, check=False,
        )
        assert res.returncode == 0, f"{src.name}: {res.stderr}"
        assert obj.is_file() and obj.stat().st_size > 0
