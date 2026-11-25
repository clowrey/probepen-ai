"""The circuit: every component and every connection. Single source of truth for the schematic
(gen_sch.py), the board (gen_pcb.py), the JLC files (export_jlc.py), and firmware pin checks.

Pin numbers match KiCad symbol pin numbers and footprint pad numbers. Unconnected pins use "NC".
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from hw.parts import PARTS

NS = uuid.UUID("a3b8d19c-4e72-4d30-9f12-7b8e5c3a1f04")
ROOT_UUID = str(uuid.uuid5(NS, "root-sheet"))


@dataclass
class Comp:
    ref: str
    part: str | None              # key into hw.parts.PARTS; None for DNP test pads / mounting holes
    pins: dict[str, str]          # pin number -> net ("NC" = intentional no-connect)
    value: str = ""
    symbol: str = ""              # override for no-part items
    footprint: str = ""
    dnp: bool = False
    block: str = ""               # mcu, power, afe, adc, logic, lcd, ui, usb
    uuid: str = field(default="")

    def __post_init__(self):
        p = PARTS.get(self.part) if self.part else None
        if p:
            self.value = self.value or p.value
            self.symbol = self.symbol or p.symbol
            self.footprint = self.footprint or p.footprint
        self.uuid = str(uuid.uuid5(NS, self.ref))

    @property
    def lcsc(self) -> str:
        return PARTS[self.part].lcsc if self.part else ""


# ---- RP2354B GPIO allocation -----------------------------------------------------------------
# Each RP2350/RP2354 PIO block has a 32-GPIO window (GPIOBASE = 0 covers GPIO0..31, or 16 covers
# GPIO16..47). Keeping MS9280 D0..D9 (0..9), CLK (10), OTR (11), STBY (12), TLV3501 (16), and
# SN74LV4T125 (17..20) inside GPIO0..31 lets PIO0 + PIO1 reach all high-speed capture & logic
# signals with GPIOBASE = 0.
# RP2354B ADC0..ADC7 live on GPIO40..47 (not GPIO26..29 as on RP2040/RP2350A).
# MS9280 is rated to 35 MSPS; with clk_sys = 140 MHz (or 150 MHz max rated / clkdiv = 2.143),
# the 2-instruction PIO capture loop hits 35.0 MSPS without overclocking.
GPIO = {
    "ADC_D0": 0,
    "ADC_D1": 1,
    "ADC_D2": 2,
    "ADC_D3": 3,
    "ADC_D4": 4,
    "ADC_D5": 5,
    "ADC_D6": 6,
    "ADC_D7": 7,
    "ADC_D8": 8,
    "ADC_D9": 9,
    "ADC_CLK": 10,
    "ADC_OTR": 11,
    "ADC_STBY": 12,
    "COMP_OUT": 16,
    "POD_CH0": 17,
    "POD_CH1": 18,
    "POD_CH2": 19,
    "POD_CH3": 20,
    "LCD_CS_N": 21,
    "LCD_SCK": 22,
    "LCD_MOSI": 23,
    "LCD_DC": 24,
    "LCD_RST_N": 25,
    "LCD_BLK_GATE_N": 26,
    "LED_DATA": 28,
    "BTN_ASK_AI_N": 29,
    "BTN_MODE_N": 30,
    "AFE_GAIN_10X": 31,
    "COMP_THR_PWM": 32,
    "AFE_DC_SENSE": 40,   # ADC0
    "COMP_THR_ADC": 41,   # ADC1
    "VBUS_SENSE": 42,     # ADC2
}

# RP2354B QFN-80 pin -> GPIO number (RP2350 Datasheet Table 1674, QFN-80 column)
RP2354B_GPIO_PIN: dict[str, int] = {
    "77": 0, "78": 1, "79": 2, "80": 3,
    "1": 4, "2": 5, "3": 6, "4": 7,
    "6": 8, "7": 9, "8": 10, "9": 11,
    "11": 12, "12": 13, "13": 14, "14": 15,
    "16": 16, "17": 17, "18": 18, "19": 19,
    "20": 20, "21": 21, "22": 22, "23": 23,
    "25": 24, "26": 25, "27": 26, "28": 27,
    "36": 28, "37": 29, "38": 30, "39": 31,
    "40": 32, "42": 33, "43": 34, "44": 35,
    "45": 36, "46": 37, "47": 38, "48": 39,
    "49": 40, "52": 41, "53": 42, "54": 43,
    "55": 44, "56": 45, "57": 46, "58": 47,
}

_GPIO_NET_BY_NUM = {
    GPIO["ADC_D0"]: "ADC_D0_MCU",
    GPIO["ADC_D1"]: "ADC_D1_MCU",
    GPIO["ADC_D2"]: "ADC_D2_MCU",
    GPIO["ADC_D3"]: "ADC_D3_MCU",
    GPIO["ADC_D4"]: "ADC_D4_MCU",
    GPIO["ADC_D5"]: "ADC_D5_MCU",
    GPIO["ADC_D6"]: "ADC_D6_MCU",
    GPIO["ADC_D7"]: "ADC_D7_MCU",
    GPIO["ADC_D8"]: "ADC_D8_MCU",
    GPIO["ADC_D9"]: "ADC_D9_MCU",
    GPIO["ADC_CLK"]: "ADC_CLK_MCU",
    GPIO["ADC_OTR"]: "ADC_OTR",
    GPIO["ADC_STBY"]: "ADC_STBY",
    GPIO["COMP_OUT"]: "COMP_OUT",
    GPIO["POD_CH0"]: "POD_CH0",
    GPIO["POD_CH1"]: "POD_CH1",
    GPIO["POD_CH2"]: "POD_CH2",
    GPIO["POD_CH3"]: "POD_CH3",
    GPIO["LCD_CS_N"]: "LCD_CS_N",
    GPIO["LCD_SCK"]: "LCD_SCK",
    GPIO["LCD_MOSI"]: "LCD_MOSI",
    GPIO["LCD_DC"]: "LCD_DC",
    GPIO["LCD_RST_N"]: "LCD_RST_N",
    GPIO["LCD_BLK_GATE_N"]: "LCD_BLK_GATE_N",
    GPIO["LED_DATA"]: "LED_DATA_MCU",
    GPIO["BTN_ASK_AI_N"]: "BTN_ASK_AI_N",
    GPIO["BTN_MODE_N"]: "BTN_MODE_N",
    GPIO["AFE_GAIN_10X"]: "AFE_GAIN_10X",
    GPIO["COMP_THR_PWM"]: "COMP_THR_PWM",
    GPIO["AFE_DC_SENSE"]: "AFE_DC_SENSE",
    GPIO["COMP_THR_ADC"]: "COMP_THR_DAC",
    GPIO["VBUS_SENSE"]: "VBUS_SENSE",
}


def _rp2354b_pins() -> dict[str, str]:
    """Complete 81-pin mapping for RP2354B QFN-80 (RP2350 Datasheet Section 14.8)."""
    pins: dict[str, str] = {
        "5": "+3V3_DIG",    # IOVDD
        "15": "+3V3_DIG",   # IOVDD
        "24": "+3V3_DIG",   # IOVDD
        "29": "+3V3_DIG",   # IOVDD
        "41": "+3V3_DIG",   # IOVDD
        "50": "+3V3_DIG",   # IOVDD
        "60": "+3V3_DIG",   # IOVDD
        "76": "+3V3_DIG",   # IOVDD
        "10": "+1V1_CORE",  # DVDD
        "32": "+1V1_CORE",  # DVDD
        "51": "+1V1_CORE",  # DVDD
        "30": "XIN",
        "31": "XOUT_RAW",
        "33": "SWCLK",
        "34": "SWDIO",
        "35": "RUN_N",
        "59": "+3V0_REF",   # ADC_AVDD (3.000 V >= 2.97 V min for full ADC spec)
        "61": "VREG_AVDD",
        "62": "GND",        # VREG_PGND
        "63": "VREG_LX",
        "64": "+3V3_DIG",   # VREG_VIN
        "65": "+1V1_CORE",  # VREG_FB
        "66": "USB_DM",
        "67": "USB_DP",
        "68": "+3V3_DIG",   # USB_OTP_VDD
        "69": "+3V3_DIG",   # QSPI_IOVDD (RP2354 internal flash runs at 3.3 V)
        "70": "NC",         # QSPI_SD3 (internal 2 MB flash)
        "71": "NC",         # QSPI_SCLK
        "72": "NC",         # QSPI_SD0
        "73": "NC",         # QSPI_SD2
        "74": "NC",         # QSPI_SD1
        "75": "QSPI_SS_N",  # ~QSPI_SS (BOOTSEL strap)
        "81": "GND",        # Exposed thermal/ground pad
    }
    for pin, gpio in RP2354B_GPIO_PIN.items():
        pins[pin] = _GPIO_NET_BY_NUM.get(gpio, "NC")
    return pins


def _tp(ref: str, net: str, block: str) -> Comp:
    return Comp(
        ref, None, {"1": net}, value=net,
        symbol="Connector:TestPoint",
        footprint="TestPoint:TestPoint_Pad_D1.0mm",
        dnp=True, block=block,
    )


C = Comp
COMPONENTS: list[Comp] = [
    # ---- USB-C, ESD, CC pulldowns, PTC fuse (usb) --------------------------------------------
    C("J1", "usbc", {
        "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND", "SH": "GND",
        "A4": "VBUS_RAW", "A9": "VBUS_RAW", "B4": "VBUS_RAW", "B9": "VBUS_RAW",
        "A5": "USB_CC1", "B5": "USB_CC2",
        "A6": "USB_CONN_DP", "B6": "USB_CONN_DP",
        "A7": "USB_CONN_DM", "B7": "USB_CONN_DM",
        "A8": "NC", "B8": "NC",
    }, block="usb"),
    C("F1", "ptc_500ma", {"1": "VBUS_RAW", "2": "+5V_VBUS"}, block="usb"),
    # SRV05-4: 1=IO1, 2=VN (GND), 3=IO2, 4=IO3, 5=VP, 6=IO4
    C("D1", "srv05_4", {
        "1": "USB_CONN_DP", "2": "GND", "3": "USB_CONN_DM",
        "4": "NC", "5": "VBUS_RAW", "6": "NC",
    }, block="usb"),
    C("R10", "r_5k1_0402", {"1": "USB_CC1", "2": "GND"}, block="usb"),
    C("R11", "r_5k1_0402", {"1": "USB_CC2", "2": "GND"}, block="usb"),
    C("R12", "r_27r_0603", {"1": "USB_CONN_DP", "2": "USB_DP"}, block="usb"),
    C("R13", "r_27r_0603", {"1": "USB_CONN_DM", "2": "USB_DM"}, block="usb"),
    C("C1", "c_100n_0402", {"1": "VBUS_RAW", "2": "GND"}, block="usb"),

    # ---- Power Tree: +3V3_DIG, +3V3_ANA, +3V0_REF, -3V3_RAW, -1V8_ANA (power) ---------------
    # RT9013-33GB SOT-23-5: 1=VIN, 2=GND, 3=EN, 4=NC/BP, 5=VOUT (Richtek DS9013-10)
    C("U9", "rt9013", {"1": "+5V_VBUS", "2": "GND", "3": "+5V_VBUS", "4": "NC", "5": "+3V3_DIG"}, block="power"),
    C("C2", "c_10u_0603", {"1": "+5V_VBUS", "2": "GND"}, block="power"),
    C("C3", "c_10u_0603", {"1": "+3V3_DIG", "2": "GND"}, block="power"),

    C("FB1", "fb_600r", {"1": "+5V_VBUS", "2": "+5V_ANA_IN"}, block="power"),
    C("U10", "rt9013", {"1": "+5V_ANA_IN", "2": "AGND", "3": "+5V_ANA_IN", "4": "NC", "5": "+3V3_ANA"}, block="power"),
    C("C4", "c_10u_0603", {"1": "+5V_ANA_IN", "2": "AGND"}, block="power"),
    C("C5", "c_10u_0603", {"1": "+3V3_ANA", "2": "AGND"}, block="power"),

    # REF3030 SOT-23-3: 1=IN, 2=OUT, 3=GND (TI SBVS032)
    C("U5", "ref3030", {"1": "+3V3_ANA", "2": "+3V0_REF", "3": "AGND"}, block="power"),
    C("C6", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="power"),
    C("C7", "c_1u_0402", {"1": "+3V0_REF", "2": "AGND"}, block="power"),
    C("C62", "c_100n_0402", {"1": "+3V0_REF", "2": "AGND"}, block="power"),

    # LM2776DBVR SOT-23-6: 1=VOUT, 2=GND, 3=VIN, 4=EN, 5=C1+, 6=C1- (TI SNVS956B)
    C("U11", "lm2776", {
        "1": "-3V3_RAW", "2": "AGND", "3": "+3V3_ANA",
        "4": "+3V3_ANA", "5": "LM2776_C1P", "6": "LM2776_C1N",
    }, block="power"),
    C("C8", "c_4u7_0603", {"1": "+3V3_ANA", "2": "AGND"}, block="power"),
    C("C9", "c_1u_0402", {"1": "LM2776_C1P", "2": "LM2776_C1N"}, block="power"),
    C("C10", "c_10u_0603", {"1": "AGND", "2": "-3V3_RAW"}, block="power"),

    # TPS72301DBVR SOT-23-5: 1=GND, 2=IN, 3=EN, 4=FB, 5=OUT (TI SLVS346C)
    # Vout = -1.186 * (1 + R14 / R15) = -1.186 * (1 + 49.9k / 100k) = -1.778 V
    # EN is bipolar (active >+1.5 V or <-1.5 V); tie to -3V3_RAW so it stays enabled once LM2776 starts
    C("U12", "tps72301", {
        "1": "AGND", "2": "-3V3_RAW", "3": "-3V3_RAW", "4": "TPS_FB", "5": "-1V8_ANA",
    }, block="power"),
    C("R14", "r_49k9_0603", {"1": "-1V8_ANA", "2": "TPS_FB"}, block="power"),
    C("R15", "r_100k_0402", {"1": "TPS_FB", "2": "AGND"}, block="power"),
    C("C11", "c_4u7_0603", {"1": "AGND", "2": "-1V8_ANA"}, block="power"),

    # Star ground bridge (AGND <-> GND) and VBUS 1:2 divider for ADC2
    C("R16", "r_0r_0603", {"1": "AGND", "2": "GND"}, block="power"),
    C("R17", "r_100k_0402", {"1": "+5V_VBUS", "2": "VBUS_SENSE"}, block="power"),
    C("R18", "r_100k_0402", {"1": "VBUS_SENSE", "2": "GND"}, block="power"),
    C("C12", "c_100n_0402", {"1": "VBUS_SENSE", "2": "GND"}, block="power"),

    # Mechanical M2 mounting holes (tied to GND plane) & rail test pads
    C("H1", None, {"1": "GND"}, value="M2_Mount", symbol="Mechanical:MountingHole_Pad",
      footprint="MountingHole:MountingHole_2.2mm_M2_Pad_Via", dnp=True, block="power"),
    C("H2", None, {"1": "GND"}, value="M2_Mount", symbol="Mechanical:MountingHole_Pad",
      footprint="MountingHole:MountingHole_2.2mm_M2_Pad_Via", dnp=True, block="power"),
    _tp("TP_5V", "+5V_VBUS", "power"),
    _tp("TP_3V3D", "+3V3_DIG", "power"),
    _tp("TP_3V3A", "+3V3_ANA", "power"),
    _tp("TP_3V0R", "+3V0_REF", "power"),
    _tp("TP_N1V8", "-1V8_ANA", "power"),
    _tp("TP_1V1", "+1V1_CORE", "power"),
    _tp("TP_AGND", "AGND", "power"),
    _tp("TP_GND2", "GND", "power"),

    # ---- MCU Core: RP2354B, decoupling, crystal, BOOTSEL, RUN, SWD (mcu) ---------------------
    C("U1", "rp2354b", _rp2354b_pins(), block="mcu"),
    # 8x IOVDD + USB_OTP_VDD + QSPI_IOVDD decoupling (10x 100nF)
    *[C(f"C{13 + i}", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="mcu") for i in range(10)],
    # Core buck regulator (VREG_VIN, VREG_AVDD RC filter, L1 3.3uH, +1V1_CORE caps, ADC_AVDD)
    C("C23", "c_4u7_0603", {"1": "+3V3_DIG", "2": "GND"}, block="mcu"),
    C("R19", "r_33r_0402", {"1": "+3V3_DIG", "2": "VREG_AVDD"}, block="mcu"),
    C("C24", "c_4u7_0603", {"1": "VREG_AVDD", "2": "GND"}, block="mcu"),
    C("L1", "l_3u3", {"1": "VREG_LX", "2": "+1V1_CORE"}, block="mcu"),
    C("C25", "c_4u7_0603", {"1": "+1V1_CORE", "2": "GND"}, block="mcu"),
    C("C26", "c_100n_0402", {"1": "+1V1_CORE", "2": "GND"}, block="mcu"),
    C("C27", "c_100n_0402", {"1": "+1V1_CORE", "2": "GND"}, block="mcu"),
    C("C28", "c_100n_0402", {"1": "+1V1_CORE", "2": "GND"}, block="mcu"),
    C("C29", "c_100n_0402", {"1": "+3V0_REF", "2": "AGND"}, block="mcu"),

    # 12 MHz crystal (X322512MSB4SI, CL=20 pF -> 2*(20 - 3.5 stray) = 33 pF load caps)
    C("Y1", "xtal_12m", {"1": "XIN", "2": "GND", "3": "XOUT_XTAL", "4": "GND"}, block="mcu"),
    C("R20", "r_1k_0402", {"1": "XOUT_RAW", "2": "XOUT_XTAL"}, block="mcu"),
    C("C30", "c_33p_0402", {"1": "XIN", "2": "GND"}, block="mcu"),
    C("C31", "c_33p_0402", {"1": "XOUT_XTAL", "2": "GND"}, block="mcu"),

    # BOOTSEL (hold MODE while plugging USB-C), RUN pull-up, SWD test pads
    C("R21", "r_1k_0402", {"1": "QSPI_SS_N", "2": "BOOT_KEY"}, block="mcu"),
    C("D6", "d_1n5819ws", {"1": "BTN_MODE_N", "2": "BOOT_KEY"}, block="mcu"),  # 1=K, 2=A
    C("R22", "r_10k_0402", {"1": "+3V3_DIG", "2": "RUN_N"}, block="mcu"),
    _tp("TP_BOOT", "BOOT_KEY", "mcu"),
    _tp("TP_RUN", "RUN_N", "mcu"),
    _tp("TP_SWCLK", "SWCLK", "mcu"),
    _tp("TP_SWDIO", "SWDIO", "mcu"),
    _tp("TP_GND1", "GND", "mcu"),

    # ---- Analog Front End (afe) --------------------------------------------------------------
    # All AFE resistors use JLC Basic 1% 100 ppm/C parts (software 2-point self-calibration)
    C("J2", "m3_boss", {"1": "PROBE_TIP"}, block="afe"),
    C("R1", "r_470k_0805", {"1": "PROBE_TIP", "2": "HV_MID"}, block="afe"),
    C("R2", "r_470k_0805", {"1": "HV_MID", "2": "V_DIV"}, block="afe"),
    C("C32", "c_1p_0402", {"1": "PROBE_TIP", "2": "V_DIV"}, block="afe"),
    C("R3", "r_39k_0603", {"1": "V_DIV", "2": "AGND"}, block="afe"),
    C("C33", "c_22p_0402", {"1": "V_DIV", "2": "AGND"}, block="afe"),
    C("CT1", None, {"1": "V_DIV", "2": "AGND"}, value="2-10pF DNP",
      symbol="Device:C_Trim", footprint="ppa:C_Trimmer_Knowles_JZ100_4.5x3.2mm",
      dnp=True, block="afe"),
    # BAV199 SOT-23: 1=A1 (-1V8_ANA), 2=K2 (+3V3_ANA), 3=K1_A2 (V_DIV)
    C("D4", "bav199", {"1": "-1V8_ANA", "2": "+3V3_ANA", "3": "V_DIV"}, block="afe"),

    # U3: OPA356 #1 unity-gain input buffer (V+=+3V3_ANA, V-=-1V8_ANA)
    C("U3", "opa356", {"1": "V_BUF", "2": "-1V8_ANA", "3": "V_DIV", "4": "V_BUF", "5": "+3V3_ANA"}, block="afe"),
    C("C34", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="afe"),
    C("C35", "c_100n_0402", {"1": "AGND", "2": "-1V8_ANA"}, block="afe"),

    # Reference string using Basic 1% parts: 40k (20k+20k) + 15k + 5k (10k||10k) = 60.0k (50 uA)
    # -> exact nominal REF_1V00 = 1.000 V and REF_0V25 = 0.250 V from +3V0_REF (3.000 V)
    C("R7A", "r_20k_0402", {"1": "+3V0_REF", "2": "REF_MID"}, block="afe"),
    C("R7B", "r_20k_0402", {"1": "REF_MID", "2": "REF_1V00"}, block="afe"),
    C("C36", "c_100n_0402", {"1": "REF_1V00", "2": "AGND"}, block="afe"),
    C("R8", "r_15k_0402", {"1": "REF_1V00", "2": "REF_0V25"}, block="afe"),
    C("C37", "c_100n_0402", {"1": "REF_0V25", "2": "AGND"}, block="afe"),
    C("R9A", "r_10k_0402", {"1": "REF_0V25", "2": "AGND"}, block="afe"),
    C("R9B", "r_10k_0402", {"1": "REF_0V25", "2": "AGND"}, block="afe"),

    # U6: RS2227XN MSOP-10 dual DPDT/SPDT switch (1=V+, 2=S, 3=D+, 4=D-, 5=GND, 6=HSD1-, 7=HSD1+, 8=HSD2-, 9=HSD2+, 10=/OE)
    # S=0 (1x): D+=REF_1V00, D-=U4_FB_1X. S=1 (10x): D+=REF_0V25, D-=U4_FB_10X.
    C("U6", "rs2227", {
        "1": "+3V3_ANA", "2": "AFE_GAIN_10X", "3": "U4_INP", "4": "V_ADC", "5": "AGND",
        "6": "U4_FB_1X", "7": "REF_1V00", "8": "U4_FB_10X", "9": "REF_0V25", "10": "AGND",
    }, block="afe"),
    C("C38", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="afe"),
    C("R23", "r_10k_0402", {"1": "AFE_GAIN_10X", "2": "GND"}, block="afe"),

    # U4: OPA356 #2 inverting level shifter/gain stage (V+=+3V3_ANA, V-=AGND)
    # R4=2k, R5=1k, R6=10k -> 1x: 1.500 V - 0.5*V_BUF; 10x: 1.500 V - 5.0*V_BUF (both 1.000..2.000 V)
    C("U4", "opa356", {"1": "V_ADC", "2": "AGND", "3": "U4_INP", "4": "U4_INN", "5": "+3V3_ANA"}, block="afe"),
    C("C39", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="afe"),
    C("R4", "r_2k_0402", {"1": "V_BUF", "2": "U4_INN"}, block="afe"),
    C("R5", "r_1k_0402", {"1": "U4_INN", "2": "U4_FB_1X"}, block="afe"),
    C("R6", "r_10k_0402", {"1": "U4_INN", "2": "U4_FB_10X"}, block="afe"),
    C("C40", "c_4p7_0402", {"1": "U4_INN", "2": "V_ADC"}, block="afe"),
    C("R24", "r_10k_0402", {"1": "V_ADC", "2": "AFE_DC_SENSE"}, block="afe"),
    C("C41", "c_10n_0402", {"1": "AFE_DC_SENSE", "2": "AGND"}, block="afe"),
    _tp("TP_VADC", "V_ADC", "afe"),

    # ---- High-Speed ADC: MS9280 SSOP-28 (adc) ------------------------------------------------
    C("R25", "r_33r_0402", {"1": "V_ADC", "2": "ADC_AIN"}, block="adc"),
    C("C42", "c_47p_0402", {"1": "ADC_AIN", "2": "AGND"}, block="adc"),
    C("U2", "ms9280", {
        "1": "AGND",       # AVSS
        "2": "+3V3_DIG",   # DRVDD
        "3": "ADC_D0",     # D0 (LSB)
        "4": "ADC_D1",     # D1
        "5": "ADC_D2",     # D2 (AD9280 D0)
        "6": "ADC_D3",     # D3
        "7": "ADC_D4",     # D4
        "8": "ADC_D5",     # D5
        "9": "ADC_D6",     # D6
        "10": "ADC_D7",    # D7
        "11": "ADC_D8",    # D8
        "12": "ADC_D9",    # D9 (MSB / AD9280 D7)
        "13": "ADC_OTR",   # OTR
        "14": "GND",       # DRVSS
        "15": "ADC_CLK",   # CLK
        "16": "GND",       # THREE-STATE (active low)
        "17": "ADC_STBY",  # STBY
        "18": "ADC_VREF",  # REFSENSE -> VREF = 1.000 V span (MS9280 Fig. 2d / Fig. 5)
        "19": "AGND",      # CLAMP (low = disabled for DC coupling)
        "20": "AGND",      # CLAMPIN
        "21": "ADC_REFTF", # REFTS
        "22": "ADC_REFTF", # REFTF
        "23": "ADC_MODE",  # MODE = 1.500 V mid-voltage -> 1.000..2.000 V range (Fig. 5)
        "24": "ADC_REFBF", # REFBF
        "25": "ADC_REFBF", # REFBS
        "26": "ADC_VREF",  # VREF
        "27": "ADC_AIN",   # AIN
        "28": "+3V3_ANA",  # AVDD
    }, block="adc"),
    *[C(f"R{26 + i}", "r_33r_0402", {"1": f"ADC_D{i}", "2": f"ADC_D{i}_MCU"}, block="adc") for i in range(10)],
    C("R36", "r_33r_0402", {"1": "ADC_CLK_MCU", "2": "ADC_CLK"}, block="adc"),
    C("R37", "r_10k_0402", {"1": "ADC_STBY", "2": "GND"}, block="adc"),
    C("R38", "r_10k_0402", {"1": "+3V0_REF", "2": "ADC_MODE"}, block="adc"),
    C("R39", "r_10k_0402", {"1": "ADC_MODE", "2": "AGND"}, block="adc"),
    C("C43", "c_100n_0402", {"1": "ADC_MODE", "2": "AGND"}, block="adc"),
    C("C44", "c_1u_0402", {"1": "ADC_VREF", "2": "AGND"}, block="adc"),
    C("C45", "c_100n_0402", {"1": "ADC_VREF", "2": "AGND"}, block="adc"),
    C("C46", "c_100n_0402", {"1": "ADC_REFTF", "2": "AGND"}, block="adc"),
    C("C47", "c_100n_0402", {"1": "ADC_REFBF", "2": "AGND"}, block="adc"),
    C("C48", "c_10u_0603", {"1": "ADC_REFTF", "2": "ADC_REFBF"}, block="adc"),
    C("C49", "c_100n_0402", {"1": "ADC_REFTF", "2": "ADC_REFBF"}, block="adc"),
    C("C50", "c_1u_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="adc"),
    C("C51", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="adc"),
    C("C52", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="adc"),

    # ---- Logic Probe Comparator & 4-Channel Side Logic Pod (logic) ---------------------------
    C("R40", "r_10k_0402", {"1": "COMP_THR_PWM", "2": "COMP_THR_MID"}, block="logic"),
    C("C53", "c_100n_0402", {"1": "COMP_THR_MID", "2": "GND"}, block="logic"),
    C("R41", "r_10k_0402", {"1": "COMP_THR_MID", "2": "COMP_THR_DAC"}, block="logic"),
    C("C54", "c_100n_0402", {"1": "COMP_THR_DAC", "2": "GND"}, block="logic"),
    C("R42", "r_100r_0402", {"1": "V_ADC", "2": "COMP_INN"}, block="logic"),
    # TLV3501AIDBVR SOT-23-6: 1=-IN, 2=V-, 3=+IN, 4=V+, 5=OUT, 6=SHDN (TI SBOS321E)
    C("U7", "tlv3501", {
        "1": "COMP_INN", "2": "GND", "3": "COMP_THR_DAC",
        "4": "+3V3_DIG", "5": "COMP_OUT", "6": "GND",
    }, block="logic"),
    C("C55", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="logic"),

    # Side 4-channel logic pod: JST-SH 6P + shared PTC + 220R + SRV05-4 (VP=+5V_VBUS) + SN74LV4T125
    C("J3", "jst_sh6", {
        "1": "GND", "2": "+3V3_POD",
        "3": "POD_CH0_RAW", "4": "POD_CH1_RAW", "5": "POD_CH2_RAW", "6": "POD_CH3_RAW",
        "MP": "GND",
    }, block="logic"),
    C("F2", "ptc_500ma", {"1": "+3V3_DIG", "2": "+3V3_POD"}, block="logic"),
    C("C56", "c_100n_0402", {"1": "+3V3_POD", "2": "GND"}, block="logic"),
    *[C(f"R{43 + i}", "r_220r_0402", {"1": f"POD_CH{i}_RAW", "2": f"POD_CH{i}_PROT"}, block="logic") for i in range(4)],
    C("D2", "srv05_4", {
        "1": "POD_CH0_PROT", "2": "GND", "3": "POD_CH1_PROT",
        "4": "POD_CH2_PROT", "5": "+5V_VBUS", "6": "POD_CH3_PROT",
    }, block="logic"),
    C("U8", "lv4t125", {
        "1": "GND", "2": "POD_CH0_PROT", "3": "POD_CH0",
        "4": "GND", "5": "POD_CH1_PROT", "6": "POD_CH1",
        "7": "GND",
        "8": "POD_CH2", "9": "POD_CH2_PROT", "10": "GND",
        "11": "POD_CH3", "12": "POD_CH3_PROT", "13": "GND",
        "14": "+3V3_DIG",
    }, block="logic"),
    C("C57", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="logic"),

    # ---- LCD: Molex 503480-0800 8P FPC + AO3401A high-side backlight (lcd) -------------------
    # AO3401A SOT-23: 1=G, 2=S, 3=D
    C("Q1", "ao3401a", {"1": "LCD_BLK_GATE_N", "2": "+3V3_DIG", "3": "LCD_BLK_SW"}, block="lcd"),
    C("R47", "r_47k_0402", {"1": "+3V3_DIG", "2": "LCD_BLK_GATE_N"}, block="lcd"),
    C("R48", "r_27r_0603", {"1": "LCD_BLK_SW", "2": "LCD_LEDA"}, block="lcd"),
    C("J4", "fpc_8p", {
        "1": "LCD_LEDA", "2": "GND", "3": "LCD_RST_N", "4": "LCD_DC",
        "5": "LCD_MOSI", "6": "LCD_SCK", "7": "+3V3_DIG", "8": "LCD_CS_N",
        "MP": "GND",
    }, block="lcd"),
    C("C58", "c_1u_0402", {"1": "+3V3_DIG", "2": "GND"}, block="lcd"),
    C("C59", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="lcd"),

    # ---- User Interface: tactile buttons + 2x WS2812B-2020-V6 (ui) ---------------------------
    C("SW1", "button", {"1": "BTN_ASK_AI_N", "2": "GND"}, value="ASK_AI", block="ui"),
    C("R49", "r_10k_0402", {"1": "+3V3_DIG", "2": "BTN_ASK_AI_N"}, block="ui"),
    C("SW2", "button", {"1": "BTN_MODE_N", "2": "GND"}, value="MODE", block="ui"),
    C("R50", "r_10k_0402", {"1": "+3V3_DIG", "2": "BTN_MODE_N"}, block="ui"),
    # 1N4148WS SOD-323: 1=K, 2=A (drops +5V_VBUS to ~4.3 V LED_VDD)
    C("D5", "d_1n4148ws", {"1": "LED_VDD", "2": "+5V_VBUS"}, block="ui"),
    C("R51", "r_220r_0402", {"1": "LED_DATA_MCU", "2": "LED1_DIN"}, block="ui"),
    # WS2812B-2020-V6: 1=DOUT, 2=VSS, 3=DIN, 4=VDD
    C("LED1", "ws2812b", {"1": "LED_CASCADE", "2": "GND", "3": "LED1_DIN", "4": "LED_VDD"}, block="ui"),
    C("C60", "c_100n_0402", {"1": "LED_VDD", "2": "GND"}, block="ui"),
    C("LED2", "ws2812b", {"1": "NC", "2": "GND", "3": "LED_CASCADE", "4": "LED_VDD"}, block="ui"),
    C("C61", "c_100n_0402", {"1": "LED_VDD", "2": "GND"}, block="ui"),
]


def _sort_key(c: Comp) -> tuple[str, int, str]:
    m = re.match(r"^([A-Za-z]+)(\d+)([A-Za-z]*)$", c.ref)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3))
    return (c.ref, 0, c.ref)


COMPONENTS.sort(key=_sort_key)
_BY_REF = {c.ref: c for c in COMPONENTS}


def comp(ref: str) -> Comp:
    return _BY_REF[ref]


def pin_net(ref: str, pin: str) -> str:
    return _BY_REF[ref].pins[pin]


NETS: dict[str, list[tuple[str, str]]] = {}
NC_PINS: list[tuple[str, str]] = []
for _c in COMPONENTS:
    for _p, _n in _c.pins.items():
        if _n == "NC":
            NC_PINS.append((_c.ref, _p))
        else:
            NETS.setdefault(_n, []).append((_c.ref, _p))

NETCLASS: dict[str, str] = {}
for _n in NETS:
    if _n in (
        "VBUS_RAW", "+5V_VBUS", "+5V_ANA_IN", "+3V3_DIG", "+3V3_ANA", "+3V3_POD",
        "+3V0_REF", "-3V3_RAW", "-1V8_ANA", "+1V1_CORE", "VREG_AVDD", "VREG_LX",
        "LED_VDD", "GND", "AGND",
    ):
        NETCLASS[_n] = "POWER"
    elif _n in ("USB_CONN_DP", "USB_CONN_DM", "USB_DP", "USB_DM"):
        NETCLASS[_n] = "USB"
    elif _n in (
        "PROBE_TIP", "HV_MID", "V_DIV", "V_BUF", "U4_INP", "U4_INN",
        "U4_FB_1X", "U4_FB_10X", "V_ADC", "ADC_AIN", "REF_MID", "REF_1V00", "REF_0V25",
        "ADC_VREF", "ADC_REFTF", "ADC_REFBF", "ADC_MODE", "AFE_DC_SENSE",
        "COMP_INN", "TPS_FB", "LM2776_C1P", "LM2776_C1N",
    ):
        NETCLASS[_n] = "ANALOG"
    elif _n.startswith(("ADC_D", "ADC_CLK")) or _n in ("COMP_OUT", "LCD_SCK", "LCD_MOSI"):
        NETCLASS[_n] = "HIGHSPEED"
    else:
        NETCLASS[_n] = "LV"
