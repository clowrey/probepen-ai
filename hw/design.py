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
# U1 is placed rotated -90 deg (regulator / USB side toward the tail), so its pins 16..48 face the
# ADC. One PIO block sees 32 consecutive GPIOs (GPIOBASE = 16 covers GPIO16..47): a 2-instruction
# `in pins, 32` loop captures both AD9288 channels in one word per sample, clocked by side-set.
#   word bits  0..7  = GPIO16..23 = ADCA_D7..D0 (bit-reversed: byte = __rbit(word) >> 24)
#   word bits 16..23 = GPIO32..39 = ADCB_D0..D7
# The pin order follows the board so the two 8-bit buses reach the MCU without crossing.
# RP2354B ADC0..ADC7 are GPIO40..47 (not 5 V tolerant): DC sense and VBUS sense. Buttons: GPIO4..7.
# POD_CH0..3 sit on 5 V tolerant GPIO0..3, the pins facing the side header (PIO1, GPIOBASE = 0).
GPIO = {
    "POD_CH0": 0,
    "POD_CH1": 1,
    "POD_CH2": 2,
    "POD_CH3": 3,
    "BTN_DOWN_N": 4,
    "BTN_UP_N": 5,
    "BTN_MODE_N": 6,
    "BTN_ASK_AI_N": 7,
    "ADC_S1": 12,
    "ADC_S2": 13,
    "LCD_BLK_GATE_N": 14,
    **{f"ADCA_D{i}": 23 - i for i in range(8)},
    "ADC_CLK": 24,
    "LCD_CS_N": 25,
    "LCD_SCK": 26,        # SPI1 SCK
    "LCD_MOSI": 27,       # SPI1 TX
    "LCD_DC": 28,
    "LCD_RST_N": 29,
    "CH1_GAIN_LO": 30,
    "CH2_GAIN_LO": 31,
    **{f"ADCB_D{i}": 32 + i for i in range(8)},
    "CH2_DC_SENSE": 40,   # ADC0
    "CH1_DC_SENSE": 41,   # ADC1
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

_GPIO_NET_NAME = {"ADC_CLK": "ADC_CLK_MCU"}
_GPIO_NET_BY_NUM = {num: _GPIO_NET_NAME.get(name, name) for name, num in GPIO.items()}


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
        "59": "ADC_AVDD",   # +3V0_REF through R23 (3.000 V >= 2.97 V min for full ADC spec)
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


_RPI_C0402 = "rpi:RP2350_80QFN_minimal__C_0402_1005Metric_small_pads"

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

    # ---- Power Tree: +3V3_DIG, +3V3_ANA, +3V0_REF (power) ------------------------------------
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
    C("C7", "c_1u_0402", {"1": "+3V0_REF", "2": "AGND"}, block="power"),

    # Star ground bridge (AGND <-> GND) and VBUS 1:2 divider for ADC2
    C("R16", "r_0r_0603", {"1": "AGND", "2": "GND"}, block="power"),
    C("R17", "r_100k_0402", {"1": "+5V_VBUS", "2": "VBUS_SENSE"}, block="power"),
    C("R18", "r_100k_0402", {"1": "VBUS_SENSE", "2": "GND"}, block="power"),
    C("C12", "c_100n_0402", {"1": "VBUS_SENSE", "2": "GND"}, block="power"),

    # Unplated 2.2 mm M2 mounting holes & rail test pads
    C("H1", None, {}, value="M2_NPTH", symbol="Mechanical:MountingHole",
      footprint="MountingHole:MountingHole_2.2mm_M2", dnp=True, block="power"),
    C("H2", None, {}, value="M2_NPTH", symbol="Mechanical:MountingHole",
      footprint="MountingHole:MountingHole_2.2mm_M2", dnp=True, block="power"),
    _tp("TP_5V", "+5V_VBUS", "power"),
    _tp("TP_3V3D", "+3V3_DIG", "power"),
    _tp("TP_3V3A", "+3V3_ANA", "power"),
    _tp("TP_3V0R", "+3V0_REF", "power"),
    _tp("TP_1V1", "+1V1_CORE", "power"),
    _tp("TP_AGND", "AGND", "power"),
    _tp("TP_GND2", "GND", "power"),

    # ---- MCU Core: RP2354B, decoupling, crystal, BOOTSEL, RUN, SWD (mcu) ---------------------
    C("U1", "rp2354b", _rp2354b_pins(), block="mcu"),
    # IOVDD decoupling: 4x 100nF (C14..C17) outside the bus fan-outs + C23 4.7uF at VREG_VIN; the
    # other IOVDD pins reach the +3V3 plane through vias inside the pad ring
    *[C(f"C{14 + i}", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="mcu") for i in range(4)],
    # Core buck regulator (VREG_VIN, VREG_AVDD RC filter, L1 3.3uH, +1V1_CORE caps, ADC_AVDD)
    # Laid out exactly as on the owner's CellKeeper board (hw/regulator_ck.py): Raspberry Pi's small-pad
    # 0402 and polarised 2016 inductor footprints let the parts sit against pins 61-65.
    C("C23", "c_4u7_0402", {"1": "+3V3_DIG", "2": "GND"}, footprint=_RPI_C0402, block="mcu"),
    C("R19", "r_33r_0402", {"1": "+3V3_DIG", "2": "VREG_AVDD"}, block="mcu"),
    C("C24", "c_4u7_0402", {"1": "VREG_AVDD", "2": "GND"}, footprint=_RPI_C0402, block="mcu"),
    C("L1", "l_3u3", {"1": "+1V1_CORE", "2": "VREG_LX"}, block="mcu"),
    C("C25", "c_4u7_0402", {"1": "+1V1_CORE", "2": "GND"}, footprint=_RPI_C0402, block="mcu"),
    C("C26", "c_100n_0402", {"1": "+1V1_CORE", "2": "GND"}, block="mcu"),
    C("C28", "c_100n_0402", {"1": "+1V1_CORE", "2": "GND"}, block="mcu"),
    # ADC_AVDD: the 3.000 V reference through its own 33R + 1uF/100nF filter, so the MCU's ADC
    # supply current does not ride on the front-end reference
    C("R23", "r_33r_0402", {"1": "+3V0_REF", "2": "ADC_AVDD"}, block="mcu"),
    C("C29", "c_100n_0402", {"1": "ADC_AVDD", "2": "GND"}, block="mcu"),
    C("C20", "c_1u_0402", {"1": "ADC_AVDD", "2": "GND"}, block="mcu"),

    # 12 MHz 2520 crystal (CL = 10 pF -> 2 * (10 - 2.5 stray) = 15 pF load caps), 1k on XOUT
    C("Y1", "xtal_12m", {"1": "XIN", "2": "GND", "3": "XOUT_XTAL", "4": "GND"}, block="mcu"),
    C("R20", "r_1k_0402", {"1": "XOUT_RAW", "2": "XOUT_XTAL"}, block="mcu"),
    C("C30", "c_15p_0402", {"1": "XIN", "2": "GND"}, block="mcu"),
    C("C31", "c_15p_0402", {"1": "XOUT_XTAL", "2": "GND"}, block="mcu"),

    # BOOTSEL (hold MODE while plugging USB-C), RUN pull-up, SWD test pads
    C("R21", "r_1k_0402", {"1": "QSPI_SS_N", "2": "BOOT_KEY"}, block="mcu"),
    C("D6", "d_1n5819ws", {"1": "BTN_MODE_N", "2": "BOOT_KEY"}, block="mcu"),  # 1=K, 2=A
    C("R22", "r_10k_0402", {"1": "+3V3_DIG", "2": "RUN_N"}, block="mcu"),
    _tp("TP_BOOT", "BOOT_KEY", "mcu"),
    _tp("TP_RUN", "RUN_N", "mcu"),
    _tp("TP_SWCLK", "SWCLK", "mcu"),
    _tp("TP_SWDIO", "SWDIO", "mcu"),
    _tp("TP_GND1", "GND", "mcu"),

    # ---- Analog Front End: Dual-Channel Single-Supply 2-Gain (CH1 & CH2) + 1.000 V Ref (afe) -
    # Nose 3-in-1 connector: WAGO 2060-453 SMD (default) + DNP KF250NH-3.81-3P + 14x2.8 mm F/B pads
    C("J5", "wago_2060_3p", {"1": "CH1_TIP", "2": "AGND", "3": "CH2_TIP"}, block="afe"),

    # Shared mid-scale reference REF_MID for the gain-stage pivots and the ADC ~AINx pins. It is
    # set equal to the dividers' resting voltage (tip at 0 V) so 0 V in sits at ADC mid-scale:
    #   divider rest  = 3.000 * (51k || 940k) / (100k + 51k || 940k)   = 0.9781 V
    #   REF_MID       = 3.000 * 1k / (2k + 33R + 33R + 1k)             = 0.9785 V
    C("R7", "r_2k_0402", {"1": "+3V0_REF", "2": "REF_T1"}, block="afe"),
    C("R7B", "r_33r_0402", {"1": "REF_T1", "2": "REF_T2"}, block="afe"),
    C("R7C", "r_33r_0402", {"1": "REF_T2", "2": "REF_MID"}, block="afe"),
    C("R8", "r_1k_0402", {"1": "REF_MID", "2": "AGND"}, block="afe"),
    C("C36", "c_1u_0402", {"1": "REF_MID", "2": "AGND"}, block="afe"),
    C("C37A", "c_100n_0402", {"1": "REF_MID", "2": "AGND"}, block="adc"),
    C("C37B", "c_100n_0402", {"1": "REF_MID", "2": "AGND"}, block="adc"),

    # Channel 1 & 2 single-supply level-shifted HV dividers (940k + 100k//51k -> 1/28.83, rest 0.978 V)
    C("R1", "r_470k_0805", {"1": "CH1_TIP", "2": "CH1_HV_MID"}, block="afe"),
    C("R2", "r_470k_0805", {"1": "CH1_HV_MID", "2": "CH1_VDIV"}, block="afe"),
    C("C32", "c_1p_0402", {"1": "CH1_TIP", "2": "CH1_VDIV"}, block="afe"),
    C("R3U", "r_100k_0402", {"1": "+3V0_REF", "2": "CH1_VDIV"}, block="afe"),
    C("R3", "r_51k_0402", {"1": "CH1_VDIV", "2": "AGND"}, block="afe"),
    C("C33", "c_22p_0402", {"1": "CH1_VDIV", "2": "AGND"}, block="afe"),
    C("CT1", None, {"1": "CH1_VDIV", "2": "AGND"}, value="2-10pF DNP",
      symbol="Device:C_Trim", footprint="ppa:C_Trimmer_Knowles_JZ100_4.5x3.2mm",
      dnp=True, block="afe"),
    C("D4", "bav199", {"1": "AGND", "2": "+3V3_ANA", "3": "CH1_VDIV"}, block="afe"),

    C("R1B", "r_470k_0805", {"1": "CH2_TIP", "2": "CH2_HV_MID"}, block="afe"),
    C("R2B", "r_470k_0805", {"1": "CH2_HV_MID", "2": "CH2_VDIV"}, block="afe"),
    C("C32B", "c_1p_0402", {"1": "CH2_TIP", "2": "CH2_VDIV"}, block="afe"),
    C("R3UB", "r_100k_0402", {"1": "+3V0_REF", "2": "CH2_VDIV"}, block="afe"),
    C("R3B", "r_51k_0402", {"1": "CH2_VDIV", "2": "AGND"}, block="afe"),
    C("C33B", "c_22p_0402", {"1": "CH2_VDIV", "2": "AGND"}, block="afe"),
    C("CT2", None, {"1": "CH2_VDIV", "2": "AGND"}, value="2-10pF DNP",
      symbol="Device:C_Trim", footprint="ppa:C_Trimmer_Knowles_JZ100_4.5x3.2mm",
      dnp=True, block="afe"),
    C("D4B", "bav199", {"1": "AGND", "2": "+3V3_ANA", "3": "CH2_VDIV"}, block="afe"),

    # U3: Shared Dual RS8752XM 250 MHz unity-gain input buffer (A=CH1, B=CH2)
    C("U3", "rs8752", {
        "1": "CH1_VBUF", "2": "CH1_VBUF", "3": "CH1_VDIV", "4": "AGND",
        "5": "CH2_VDIV", "6": "CH2_VBUF", "7": "CH2_VBUF", "8": "+3V3_ANA",
    }, block="afe"),
    C("C34", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="afe"),

    # U4: Shared Dual RS8752XM inverting 2-gain PGA around REF_MID + U6 74LVC2G66DP gain switch
    # High gain (switch off): Rf = 1.5k / Rg = 510R = 2.941x          -> +/-5.0 V at the tip
    # Low gain  (switch on):  Rf = 1.5k || (330R + 6R) = 274R, 0.538x -> +/-27 V at the tip
    # The input buffer accepts 0.05..2.2 V at CHx_VDIV, i.e. -26.8 V..+35.2 V at the tip, so the
    # low range is usable to about +/-26 V.
    C("U4", "rs8752", {
        "1": "CH1_OUT", "2": "CH1_INV", "3": "REF_MID", "4": "AGND",
        "5": "REF_MID", "6": "CH2_INV", "7": "CH2_OUT", "8": "+3V3_ANA",
    }, block="afe"),
    C("C39", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="afe"),

    C("R4A", "r_510r_0402", {"1": "CH1_VBUF", "2": "CH1_INV"}, block="afe"),
    C("R5A", "r_1k5_0402", {"1": "CH1_INV", "2": "CH1_OUT"}, block="afe"),
    C("C40A", "c_4p7_0402", {"1": "CH1_INV", "2": "CH1_OUT"}, block="afe"),
    C("R6A", "r_330r_0402", {"1": "CH1_INV", "2": "CH1_SW_LO"}, block="afe"),
    C("R24", "r_10k_0402", {"1": "CH1_OUT", "2": "CH1_DC_SENSE"}, block="afe"),
    C("C41", "c_10n_0402", {"1": "CH1_DC_SENSE", "2": "GND"}, block="mcu"),   # at the MCU ADC pin
    _tp("TP_CH1", "CH1_OUT", "afe"),

    C("R4B", "r_510r_0402", {"1": "CH2_VBUF", "2": "CH2_INV"}, block="afe"),
    C("R5B", "r_1k5_0402", {"1": "CH2_INV", "2": "CH2_OUT"}, block="afe"),
    C("C40B", "c_4p7_0402", {"1": "CH2_INV", "2": "CH2_OUT"}, block="afe"),
    C("R6B", "r_330r_0402", {"1": "CH2_INV", "2": "CH2_SW_LO"}, block="afe"),
    C("R24B", "r_10k_0402", {"1": "CH2_OUT", "2": "CH2_DC_SENSE"}, block="afe"),
    C("C41B", "c_10n_0402", {"1": "CH2_DC_SENSE", "2": "GND"}, block="mcu"),  # at the MCU ADC pin
    _tp("TP_CH2", "CH2_OUT", "afe"),
    _tp("TP_REFM", "REF_MID", "afe"),

    # U6: Nexperia 74LVC2G66DP dual independent SPST gain switch (1E=pin7 CH1, 2E=pin3 CH2).
    # The switches are bilateral, so Y/Z are assigned to suit the layout (the OUT pin is the one nearer U4).
    C("U6", "lvc2g66", {
        "1": "CH1_SW_LO", "2": "CH1_OUT", "3": "CH2_GAIN_LO", "4": "AGND",
        "5": "CH2_OUT", "6": "CH2_SW_LO", "7": "CH1_GAIN_LO", "8": "+3V3_ANA",
    }, block="afe"),

    # ---- Dual High-Speed ADC: AD9288BSTZ-80 / MXT2088 LQFP-48 DNP (user hand-solder) (adc) -
    C("R25A", "r_33r_0402", {"1": "CH1_OUT", "2": "ADCA_INP"}, block="adc"),
    C("C42", "c_100p_0402", {"1": "ADCA_INP", "2": "AGND"}, block="adc"),  # 33R x 100pF = 48 MHz
    C("R25B", "r_33r_0402", {"1": "CH2_OUT", "2": "ADCB_INP"}, block="adc"),
    C("C42B", "c_100p_0402", {"1": "ADCB_INP", "2": "AGND"}, block="adc"),
    C("U2", None, {
        "1": "AGND",      # GND (analog side)
        "2": "ADCA_INP",  # AINA
        "3": "REF_MID",  # ~AINA (single-ended mid-scale reference)
        "4": "AGND",      # DFS (0 = offset binary)
        "5": "ADC_VREF",  # REFINA
        "6": "ADC_VREF",  # REFOUT (1.25 V internal ref -> REFINA/REFINB = 1.024 Vpp diff span)
        "7": "ADC_VREF",  # REFINB
        "8": "ADC_S1",    # S1
        "9": "ADC_S2",    # S2
        "10": "REF_MID", # ~AINB (single-ended mid-scale reference)
        "11": "ADCB_INP", # AINB
        "12": "AGND",     # GND (analog side)
        "13": "+3V3_ANA", # VD
        "14": "ADC_ENCB", # ENCB
        "15": "+3V3_DIG", # VDD
        "16": "GND",      # GND (digital side)
        "17": "ADCB_D7",  # D7B (MSB)
        "18": "ADCB_D6",  # D6B
        "19": "ADCB_D5",  # D5B
        "20": "ADCB_D4",  # D4B
        "21": "ADCB_D3",  # D3B
        "22": "ADCB_D2",  # D2B
        "23": "ADCB_D1",  # D1B
        "24": "ADCB_D0",  # D0B (LSB)
        "25": "NC",
        "26": "NC",
        "27": "GND",      # GND
        "28": "+3V3_DIG", # VDD
        "29": "AGND",     # GND (near VD)
        "30": "+3V3_ANA", # VD
        "31": "+3V3_ANA", # VD
        "32": "AGND",     # GND (near VD)
        "33": "+3V3_DIG", # VDD
        "34": "GND",      # GND
        "35": "NC",
        "36": "NC",
        "37": "ADCA_D0",  # D0A (LSB)
        "38": "ADCA_D1",  # D1A
        "39": "ADCA_D2",  # D2A
        "40": "ADCA_D3",  # D3A
        "41": "ADCA_D4",  # D4A
        "42": "ADCA_D5",  # D5A
        "43": "ADCA_D6",  # D6A
        "44": "ADCA_D7",  # D7A (MSB)
        "45": "GND",      # GND
        "46": "+3V3_DIG", # VDD
        "47": "ADC_ENCA", # ENCA
        "48": "+3V3_ANA", # VD
    }, value="AD9288BSTZ-80 DNP", symbol="ppa:AD9288", footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm",
      dnp=True, block="adc"),
    # Direct 16-bit data bus to RP2354B over L2 GND plane; R36A/R36B damp the Y-split clock lines
    C("R36A", "r_33r_0402", {"1": "ADC_CLK_MCU", "2": "ADC_ENCA"}, block="adc"),
    C("R36B", "r_33r_0402", {"1": "ADC_CLK_MCU", "2": "ADC_ENCB"}, block="adc"),
    # AD9288 reference & consolidated supply decoupling (4-layer power/ground planes)
    C("C44", "c_1u_0402", {"1": "ADC_VREF", "2": "AGND"}, block="adc"),
    C("C46", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="adc"),
    C("C47", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="adc"),
    C("C49", "c_100n_0402", {"1": "+3V3_ANA", "2": "AGND"}, block="adc"),
    C("C52A", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="adc"),
    C("C52B", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="adc"),
    C("C52C", "c_100n_0402", {"1": "+3V3_DIG", "2": "GND"}, block="adc"),

    # ---- 6-Pin Bidirectional GPIO Expansion Header (logic) ----------------------------------
    # JST-SH 6P + shared PTC + 220R + SRV05-4 (VP=+5V_VBUS) direct to 5V-tolerant GPIO0..3
    C("J3", "jst_sh6", {
        "1": "GND", "2": "+3V3_POD",
        "3": "POD_CH0_RAW", "4": "POD_CH1_RAW", "5": "POD_CH2_RAW", "6": "POD_CH3_RAW",
        "MP": "GND",
    }, block="logic"),
    C("F2", "ptc_500ma", {"1": "+3V3_DIG", "2": "+3V3_POD"}, block="logic"),
    C("C56", "c_100n_0402", {"1": "+3V3_POD", "2": "GND"}, block="logic"),
    *[C(f"R{43 + i}", "r_220r_0402", {"1": f"POD_CH{i}_RAW", "2": f"POD_CH{i}"}, block="logic") for i in range(4)],
    C("D2", "srv05_4", {
        "1": "POD_CH0_RAW", "2": "GND", "3": "POD_CH1_RAW",
        "4": "POD_CH2_RAW", "5": "+5V_VBUS", "6": "POD_CH3_RAW",
    }, block="logic"),

    # ---- LCD: 1.14"/0.96" 13P 0.70 mm Solder FPC on B.Cu + AO3401A high-side backlight (lcd) -
    # AO3401A SOT-23: 1=G, 2=S, 3=D
    C("Q1", "ao3401a", {"1": "LCD_BLK_GATE_N", "2": "+3V3_DIG", "3": "LCD_BLK_SW"}, block="lcd"),
    C("R47", "r_47k_0402", {"1": "+3V3_DIG", "2": "LCD_BLK_GATE_N"}, block="lcd"),
    C("R48", "r_27r_0603", {"1": "LCD_BLK_SW", "2": "LCD_LEDA"}, block="lcd"),
    C("J4", None, {
        "1": "NC", "2": "NC", "3": "LCD_MOSI", "4": "LCD_SCK",
        "5": "LCD_DC", "6": "LCD_RST_N", "7": "LCD_CS_N", "8": "GND",
        "9": "NC", "10": "+3V3_DIG", "11": "GND", "12": "LCD_LEDA", "13": "GND",
    }, value="LCD_13P_0.7mm", symbol="Connector_Generic:Conn_01x13",
      footprint="ppa:LCD_FPC_13P_P0.70mm_Solder", dnp=True, block="lcd"),
    C("C58", "c_1u_0402", {"1": "+3V3_DIG", "2": "GND"}, block="lcd"),

    # ---- User Interface: 4x side buttons + 2x front XL-3806UWC worklight LEDs (ui) --------
    C("SW1", "button", {"1": "BTN_ASK_AI_N", "2": "GND", "3": "GND", "4": "GND"}, value="ASK_AI", block="ui"),
    C("SW2", "button", {"1": "BTN_MODE_N", "2": "GND", "3": "GND", "4": "GND"}, value="MODE", block="ui"),
    C("SW3", "button", {"1": "BTN_UP_N", "2": "GND", "3": "GND", "4": "GND"}, value="UP", block="ui"),
    C("SW4", "button", {"1": "BTN_DOWN_N", "2": "GND", "3": "GND", "4": "GND"}, value="DOWN", block="ui"),
    # Always-on DC white worklight LEDs on B.Cu nose tip (220R ~8-9 mA each; DNP hand-solder).
    # They are fed from the filtered analog 5 V and return to AGND: a constant current, so no
    # digital supply or ground has to run under the high-impedance input section.
    C("R51A", "r_220r_0402", {"1": "+5V_ANA_IN", "2": "LED1_A"}, block="ui"),
    C("R51B", "r_220r_0402", {"1": "+5V_ANA_IN", "2": "LED2_A"}, block="ui"),
    C("LED1", None, {"1": "AGND", "2": "LED1_A"}, value="XL-3806UWC DNP",
      symbol="Device:LED", footprint="ppa:LED_Side_XL3806_3.8x1.0mm", dnp=True, block="ui"),
    C("LED2", None, {"1": "AGND", "2": "LED2_A"}, value="XL-3806UWC DNP",
      symbol="Device:LED", footprint="ppa:LED_Side_XL3806_3.8x1.0mm", dnp=True, block="ui"),
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
        "+3V0_REF", "+1V1_CORE", "VREG_AVDD", "VREG_LX", "ADC_AVDD",
        "GND", "AGND",
    ):
        NETCLASS[_n] = "POWER"
    elif _n in ("USB_CONN_DP", "USB_CONN_DM", "USB_DP", "USB_DM"):
        NETCLASS[_n] = "USB"
    elif _n in (
        "CH1_TIP", "CH1_HV_MID", "CH1_VDIV", "CH1_VBUF", "CH1_INV", "CH1_OUT", "CH1_SW_LO",
        "CH1_DC_SENSE", "ADCA_INP",
        "CH2_TIP", "CH2_HV_MID", "CH2_VDIV", "CH2_VBUF", "CH2_INV", "CH2_OUT", "CH2_SW_LO",
        "CH2_DC_SENSE", "ADCB_INP",
        "REF_MID", "REF_T1", "REF_T2", "ADC_VREF",
    ):
        NETCLASS[_n] = "ANALOG"
    elif _n.startswith(("ADCA_D", "ADCB_D", "ADC_CLK", "ADC_ENC")) or _n in ("LCD_SCK", "LCD_MOSI"):
        NETCLASS[_n] = "HIGHSPEED"
    else:
        NETCLASS[_n] = "LV"
