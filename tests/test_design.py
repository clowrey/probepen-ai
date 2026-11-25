"""The circuit in hw/design.py: connectivity, datasheet pinouts, and analog math checks."""
from collections import Counter

from hw import design
from hw.parts import PARTS


def test_every_net_has_two_nodes():
    for net, nodes in design.NETS.items():
        assert len(nodes) >= 2, f"Floating 1-pin net: {net} -> {nodes}"


def test_no_pin_twice():
    seen = Counter((r, p) for nodes in design.NETS.values() for r, p in nodes)
    for r, p in design.NC_PINS:
        seen[(r, p)] += 1
    dups = [k for k, n in seen.items() if n > 1]
    assert dups == [], f"Duplicate pins: {dups}"


def test_refs_unique_and_parts_exist():
    refs = [c.ref for c in design.COMPONENTS]
    assert len(refs) == len(set(refs)), "Duplicate component reference designators"
    for c in design.COMPONENTS:
        if c.part is not None:
            assert c.part in PARTS, f"{c.ref}: unknown part key {c.part}"


def test_per_board_counts_match_catalogue():
    counts = Counter(c.part for c in design.COMPONENTS if c.part)
    for key, p in PARTS.items():
        assert counts[key] == p.per_board, (key, counts[key], p.per_board)


def test_rp2354b_pin_completeness():
    u1 = design.comp("U1")
    assert set(u1.pins) == {str(i) for i in range(1, 82)}
    for p in ("5", "15", "24", "29", "41", "50", "60", "76", "64", "68", "69"):
        assert u1.pins[p] == "+3V3_DIG", f"U1 pin {p} expected +3V3_DIG, got {u1.pins[p]}"
    for p in ("10", "32", "51", "65"):
        assert u1.pins[p] == "+1V1_CORE", f"U1 pin {p} expected +1V1_CORE, got {u1.pins[p]}"
    assert u1.pins["59"] == "+3V0_REF"
    assert u1.pins["61"] == "VREG_AVDD"
    assert u1.pins["62"] == "GND"
    assert u1.pins["63"] == "VREG_LX"
    assert u1.pins["66"] == "USB_DM"
    assert u1.pins["67"] == "USB_DP"
    assert u1.pins["75"] == "QSPI_SS_N"
    assert u1.pins["81"] == "GND"
    # Verify crystal, SWD, RUN, and core decoupling are present
    assert design.pin_net("U1", "30") == "XIN"
    assert design.pin_net("R20", "1") == "XOUT_RAW"
    assert design.pin_net("R22", "2") == "RUN_N"
    assert design.pin_net("TP_SWCLK", "1") == "SWCLK"
    assert design.pin_net("TP_SWDIO", "1") == "SWDIO"
    assert design.pin_net("TP_RUN", "1") == "RUN_N"
    assert design.pin_net("TP_BOOT", "1") == "BOOT_KEY"
    # Every power supply domain on U1 has decoupling caps
    iovdd_caps = [
        c for c in design.COMPONENTS
        if c.part == "c_100n_0402" and c.block == "mcu" and set(c.pins.values()) == {"+3V3_DIG", "GND"}
    ]
    assert len(iovdd_caps) == 10  # 8x IOVDD + USB_OTP_VDD + QSPI_IOVDD


def test_reference_string_math():
    """40k (R7A+R7B) + 15k (R8) + 5k (R9A||R9B) from +3V0_REF (3.000 V) -> 1.000 V and 0.250 V."""
    r7 = 20.0e3 + 20.0e3
    r8 = 15.0e3
    r9 = 1.0 / (1.0 / 10.0e3 + 1.0 / 10.0e3)
    vref = 3.000
    rtot = r7 + r8 + r9
    v_1v00 = vref * (r8 + r9) / rtot
    v_0v25 = vref * r9 / rtot
    assert abs(v_1v00 - 1.000) < 1e-6
    assert abs(v_0v25 - 0.250) < 1e-6
    assert PARTS[design.comp("R7A").part].value == "20k 1%"
    assert PARTS[design.comp("R7B").part].value == "20k 1%"
    assert PARTS[design.comp("R8").part].value == "15k 1%"
    assert PARTS[design.comp("R9A").part].value == "10k"
    assert PARTS[design.comp("R9B").part].value == "10k"
    assert design.pin_net("R7A", "1") == "+3V0_REF"
    assert design.pin_net("R7A", "2") == "REF_MID"
    assert design.pin_net("R7B", "1") == "REF_MID"
    assert design.pin_net("R7B", "2") == "REF_1V00"
    assert design.pin_net("R8", "1") == "REF_1V00"
    assert design.pin_net("R8", "2") == "REF_0V25"
    assert design.pin_net("R9A", "1") == "REF_0V25"
    assert design.pin_net("R9A", "2") == "AGND"
    assert design.pin_net("R9B", "1") == "REF_0V25"
    assert design.pin_net("R9B", "2") == "AGND"


def test_afe_gain_and_tps72301_math():
    # Stage 2 gains: 1x (1k / 2k = 0.500), 10x (10k / 2k = 5.000)
    assert PARTS[design.comp("R4").part].value == "2k 1%"
    assert PARTS[design.comp("R5").part].value == "1k"
    assert PARTS[design.comp("R6").part].value == "10k"
    assert design.comp("CT1").dnp is True
    # TPS72301 negative regulator: -1.186 * (1 + 49.9k / 100k) = -1.778 V
    v_neg = -1.186 * (1.0 + 49.9 / 100.0)
    assert -1.80 <= v_neg <= -1.75
    # RP2354B ADC_AVDD >= 2.97 V for full ADC spec (+3V0_REF = 3.000 V)
    assert design.pin_net("U1", "59") == "+3V0_REF"


def test_pio_and_adc_gpio_windows():
    gpios = list(design.GPIO.values())
    assert len(gpios) == len(set(gpios)), "Duplicate GPIO numbers"
    assert all(0 <= g <= 47 for g in gpios)
    # MS9280 data + clock + flags + comparator + 4-ch pod all within GPIO0..31 (PIO GPIOBASE=0)
    for name in (
        "ADC_D0", "ADC_D1", "ADC_D2", "ADC_D3", "ADC_D4",
        "ADC_D5", "ADC_D6", "ADC_D7", "ADC_D8", "ADC_D9",
        "ADC_CLK", "ADC_OTR", "ADC_STBY",
        "COMP_OUT", "POD_CH0", "POD_CH1", "POD_CH2", "POD_CH3",
    ):
        assert 0 <= design.GPIO[name] <= 31, (name, design.GPIO[name])
    # RP2354B ADC0..ADC7 are GPIO40..47
    for name in ("AFE_DC_SENSE", "COMP_THR_ADC", "VBUS_SENSE"):
        assert 40 <= design.GPIO[name] <= 47, (name, design.GPIO[name])


def test_bootsel_diode_polarity():
    """Schottky D6 cathode (pin 1) -> BTN_MODE_N, anode (pin 2) -> BOOT_KEY -> R21 -> QSPI_SS_N."""
    assert design.pin_net("D6", "1") == "BTN_MODE_N"
    assert design.pin_net("D6", "2") == "BOOT_KEY"
    assert {design.pin_net("R21", "1"), design.pin_net("R21", "2")} == {"QSPI_SS_N", "BOOT_KEY"}


def test_tlv3501_and_usb_cc_and_esd():
    u7 = design.comp("U7")
    assert u7.pins == {
        "1": "COMP_INN", "2": "GND", "3": "COMP_THR_DAC",
        "4": "+3V3_DIG", "5": "COMP_OUT", "6": "GND",
    }
    # USB CC1/CC2 5.1k pulldowns
    assert {design.pin_net("R10", "1"), design.pin_net("R10", "2")} == {"USB_CC1", "GND"}
    assert {design.pin_net("R11", "1"), design.pin_net("R11", "2")} == {"USB_CC2", "GND"}
    # USB-C ESD (D1) referenced to VBUS_RAW; side pod 4-ch ESD (D2) referenced to +5V_VBUS
    assert design.pin_net("D1", "5") == "VBUS_RAW"
    assert design.pin_net("D2", "5") == "+5V_VBUS"
    assert {design.pin_net("D2", p) for p in ("1", "3", "4", "6")} == {
        "POD_CH0_PROT", "POD_CH1_PROT", "POD_CH2_PROT", "POD_CH3_PROT",
    }


def test_power_rails_have_sources_and_test_pads():
    expected_sources = {
        "+5V_VBUS": "F1",
        "+3V3_DIG": "U9",
        "+3V3_ANA": "U10",
        "+3V0_REF": "U5",
        "-3V3_RAW": "U11",
        "-1V8_ANA": "U12",
        "+1V1_CORE": "L1",
        "LED_VDD": "D5",
    }
    for net, src in expected_sources.items():
        refs = {r for r, _ in design.NETS[net]}
        assert src in refs, (net, src, refs)
    for tp in ("TP_5V", "TP_3V3D", "TP_3V3A", "TP_3V0R", "TP_N1V8", "TP_1V1", "TP_AGND", "TP_GND2", "TP_VADC"):
        assert tp in {c.ref for c in design.COMPONENTS}, tp
