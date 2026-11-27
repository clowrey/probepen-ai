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

    assert core_lib.afe_autorange_select(0, 2.4, False) == 1
    assert core_lib.afe_autorange_select(0, 0.5, True) == 1
    assert core_lib.afe_autorange_select(1, 1.5, False) == 0


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

    fb = (ctypes.c_uint16 * (160 * 80))()
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
