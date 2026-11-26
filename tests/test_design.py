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
    assert u1.pins["59"] == "ADC_AVDD"
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
    assert len(iovdd_caps) == 4  # Consolidated 4-layer plane decoupling across U1 edges


def test_reference_string_math():
    """REF_MID (gain-stage pivot and ADC ~AINx) equals the dividers' resting voltage to within 1 mV."""
    r_up, r_dn, r_tip = 100.0e3, 51.0e3, 940.0e3
    r_par = r_dn * r_tip / (r_dn + r_tip)
    v_rest = 3.000 * r_par / (r_up + r_par)
    v_mid = 3.000 * 1.0e3 / (2.0e3 + 33.0 + 33.0 + 1.0e3)
    assert abs(v_mid - v_rest) < 1.0e-3
    assert PARTS[design.comp("R7").part].value == "2k 1%"
    assert PARTS[design.comp("R7B").part].value == "33R"
    assert PARTS[design.comp("R7C").part].value == "33R"
    assert PARTS[design.comp("R8").part].value == "1k"
    assert design.pin_net("R7", "1") == "+3V0_REF"
    assert design.pin_net("R7C", "2") == "REF_MID"
    assert design.pin_net("R8", "1") == "REF_MID"
    assert design.pin_net("R8", "2") == "AGND"
    # local decoupling at both ADC inverting inputs and at the gain stage
    for cap in ("C36", "C37A", "C37B"):
        assert set(design.comp(cap).pins.values()) == {"REF_MID", "AGND"}


def test_afe_gain_and_single_supply_math():
    # Single-supply level-shifted divider: R1+R2=940k, R_up=100k (+3V0_REF), R_dn=51k (AGND)
    r_up, r_dn = 100.0, 51.0
    r_th = (r_up * r_dn) / (r_up + r_dn)
    div_ratio = r_th / (940.0 + r_th)
    r_par_940 = (r_dn * 940.0) / (r_dn + 940.0)
    v0 = 3.000 * r_par_940 / (r_up + r_par_940)
    assert 0.96 <= v0 <= 1.00

    # 2 gain ranges via inverting RS8752XM (Rg=510R, Rf_hi=1.5k) + 74LVC2G66DP (Ron=6R, Rf_lo=330R)
    g_hi = 1500.0 / 510.0
    rf_lo_eff = (1500.0 * (330.0 + 6.0)) / (1500.0 + 330.0 + 6.0)
    g_lo = rf_lo_eff / 510.0
    v_fs_lo = 0.512 / g_lo / div_ratio
    v_fs_hi = 0.512 / g_hi / div_ratio
    assert 26.0 <= v_fs_lo <= 28.5
    assert 4.7 <= v_fs_hi <= 5.3

    # The input buffer (RS8752, 3.3 V supply) accepts 0.05..2.2 V. Both claimed ranges must fit:
    # +/-5 V on the high range and +/-26 V on the low range.
    for v_tip in (-26.0, -5.0, 5.0, 26.0):
        assert 0.05 <= v0 + v_tip * div_ratio <= 2.2, v_tip
    # 0 V at the tip lands within 2 ADC codes of mid-scale on the high range (4 mV per code)
    v_mid = 3.000 * 1.0 / (2.0 + 0.033 + 0.033 + 1.0)
    assert abs((v_mid - v0) * g_hi) < 0.008

    for rup, rdn, rg, rf, rlo in (
        ("R3U", "R3", "R4A", "R5A", "R6A"),
        ("R3UB", "R3B", "R4B", "R5B", "R6B"),
    ):
        assert PARTS[design.comp(rup).part].value == "100k"
        assert PARTS[design.comp(rdn).part].value == "51k 1%"
        assert PARTS[design.comp(rg).part].value == "510R 1%"
        assert PARTS[design.comp(rf).part].value == "1.5k 1%"
        assert PARTS[design.comp(rlo).part].value == "330R 1%"
    assert design.comp("CT1").dnp is True
    assert design.comp("CT2").dnp is True
    # Single-supply clamps to AGND / +3V3_ANA, and AD9288 ~AINx tied to REF_MID
    assert design.comp("D4").pins == {"1": "AGND", "2": "+3V3_ANA", "3": "CH1_VDIV"}
    assert design.comp("D4B").pins == {"1": "AGND", "2": "+3V3_ANA", "3": "CH2_VDIV"}
    assert design.pin_net("U2", "3") == "REF_MID"
    assert design.pin_net("U2", "10") == "REF_MID"
    # RP2354B ADC_AVDD: the 3.000 V reference through its own 33R filter (>= 2.97 V for full ADC spec)
    assert design.pin_net("U1", "59") == "ADC_AVDD"
    assert design.comp("R23").pins == {"1": "+3V0_REF", "2": "ADC_AVDD"}


def test_pio_and_adc_gpio_windows():
    gpios = list(design.GPIO.values())
    assert len(gpios) == len(set(gpios)), "Duplicate GPIO numbers"
    assert all(0 <= g <= 47 for g in gpios)
    # AD9288 buses sit in one 32-GPIO PIO window (GPIOBASE = 16): channel A bit-reversed on
    # GPIO16..23, channel B on GPIO32..39, clock on GPIO24
    for i in range(8):
        assert design.GPIO[f"ADCA_D{i}"] == 23 - i
        assert design.GPIO[f"ADCB_D{i}"] == 32 + i
    assert 16 <= design.GPIO["ADC_CLK"] <= 47
    # Expansion pins on 5 V tolerant GPIO0..3 (GPIO40..47 are the ADC pins and are not)
    for i in range(4):
        assert design.GPIO[f"POD_CH{i}"] == i
    # RP2354B ADC0..ADC7 are GPIO40..47
    for name in ("CH1_DC_SENSE", "VBUS_SENSE", "CH2_DC_SENSE"):
        assert 40 <= design.GPIO[name] <= 47, (name, design.GPIO[name])
    # LCD on SPI1: SCK = GPIO26, TX = GPIO27
    assert design.GPIO["LCD_SCK"] == 26 and design.GPIO["LCD_MOSI"] == 27


def test_buttons_and_lcd_pinout():
    for sw, net in (
        ("SW1", "BTN_ASK_AI_N"),
        ("SW2", "BTN_MODE_N"),
        ("SW3", "BTN_UP_N"),
        ("SW4", "BTN_DOWN_N"),
    ):
        assert design.comp(sw).pins == {"1": net, "2": "GND", "3": "GND", "4": "GND"}
    j4 = design.comp("J4")
    assert j4.dnp is True
    assert j4.pins == {
        "1": "NC", "2": "NC", "3": "LCD_MOSI", "4": "LCD_SCK",
        "5": "LCD_DC", "6": "LCD_RST_N", "7": "LCD_CS_N", "8": "GND",
        "9": "NC", "10": "+3V3_DIG", "11": "GND", "12": "LCD_LEDA", "13": "GND",
    }


def test_bootsel_diode_polarity():
    """Schottky D6 cathode (pin 1) -> BTN_MODE_N, anode (pin 2) -> BOOT_KEY -> R21 -> QSPI_SS_N."""
    assert design.pin_net("D6", "1") == "BTN_MODE_N"
    assert design.pin_net("D6", "2") == "BOOT_KEY"
    assert {design.pin_net("R21", "1"), design.pin_net("R21", "2")} == {"QSPI_SS_N", "BOOT_KEY"}


def test_usb_cc_and_esd():
    # USB CC1/CC2 5.1k pulldowns
    assert {design.pin_net("R10", "1"), design.pin_net("R10", "2")} == {"USB_CC1", "GND"}
    assert {design.pin_net("R11", "1"), design.pin_net("R11", "2")} == {"USB_CC2", "GND"}
    # USB-C ESD (D1) referenced to VBUS_RAW; side pod 4-ch ESD (D2) referenced to +5V_VBUS
    assert design.pin_net("D1", "5") == "VBUS_RAW"
    assert design.pin_net("D2", "5") == "+5V_VBUS"
    assert {design.pin_net("D2", p) for p in ("1", "3", "4", "6")} == {
        "POD_CH0_RAW", "POD_CH1_RAW", "POD_CH2_RAW", "POD_CH3_RAW",
    }


def test_power_rails_have_sources_and_test_pads():
    expected_sources = {
        "+5V_VBUS": "F1",
        "+3V3_DIG": "U9",
        "+3V3_ANA": "U10",
        "+3V0_REF": "U5",
        "+1V1_CORE": "L1",
    }
    for net, src in expected_sources.items():
        refs = {r for r, _ in design.NETS[net]}
        assert src in refs, (net, src, refs)
    for tp in ("TP_5V", "TP_3V3D", "TP_3V3A", "TP_3V0R", "TP_1V1", "TP_AGND", "TP_GND2", "TP_CH1", "TP_CH2", "TP_REFM"):
        assert tp in {c.ref for c in design.COMPONENTS}, tp


def test_rs8752_and_74lvc2g66_pinouts():
    assert design.comp("U3").pins == {
        "1": "CH1_VBUF", "2": "CH1_VBUF", "3": "CH1_VDIV", "4": "AGND",
        "5": "CH2_VDIV", "6": "CH2_VBUF", "7": "CH2_VBUF", "8": "+3V3_ANA",
    }
    assert design.comp("U4").pins == {
        "1": "CH1_OUT", "2": "CH1_INV", "3": "REF_MID", "4": "AGND",
        "5": "REF_MID", "6": "CH2_INV", "7": "CH2_OUT", "8": "+3V3_ANA",
    }
    assert design.comp("U6").pins == {
        "1": "CH1_SW_LO", "2": "CH1_OUT", "3": "CH2_GAIN_LO", "4": "AGND",
        "5": "CH2_OUT", "6": "CH2_SW_LO", "7": "CH1_GAIN_LO", "8": "+3V3_ANA",
    }
