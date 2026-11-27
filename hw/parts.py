"""Part catalogue: one entry per distinct orderable part.

Every LCSC code, library type (Basic / Preferred Extended / Extended), package, and datasheet
reference was verified against JLCPCB's live catalog and official component datasheets.
`per_board` is enforced against `hw/design.py` by `tests/test_design.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Part:
    key: str
    lcsc: str
    symbol: str          # KiCad 10 standard library name, or "ppa:<Name>" for one we generate in Stage 2
    footprint: str       # KiCad 10 standard footprint, or "ppa:<Name>"
    value: str
    rotation: int = 0    # JLC CPL rotation offset vs KiCad
    tht: bool = False
    per_board: int = 1
    mpn: str = ""
    basic: bool = False  # JLC Basic part
    extra: dict = field(default_factory=dict)   # datasheet ratings, datasheet URL, notes


_R0402 = "Resistor_SMD:R_0402_1005Metric"
_R0603 = "Resistor_SMD:R_0603_1608Metric"
_R0805 = "Resistor_SMD:R_0805_2012Metric"
_C0402 = "Capacitor_SMD:C_0402_1005Metric"
_C0603 = "Capacitor_SMD:C_0603_1608Metric"

_LIST = [
    # --- Active ICs ---------------------------------------------------------------------------
    Part(
        "rp2354b", "C39843328", "MCU_RaspberryPi:RP2354B",
        "rpi:RP2350_80QFN_minimal__RP2350-QFN-80-1EP_10x10_P0.4mm_EP3.4x3.4mm_ThermalVias",
        "RP2354B", mpn="RP2354B", basic=False, per_board=1,
        extra={
            "datasheet": "https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf",
            "notes": "QFN-80 S14.8, 2 MB internal QSPI flash, 150 MHz max clk_sys, ADC_AVDD >= 2.97 V, VREG_AVDD 3.135-3.63 V",
        },
    ),
    Part(
        "rs8752", "C255421", "ppa:RS8752",
        "Package_SO:MSOP-8_3x3mm_P0.65mm",
        "RS8752XM", mpn="RS8752XM", basic=False, per_board=2, rotation=90,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2304140030_Jiangsu-RUNIC-Tech-RS8752XM_C255421.pdf",
            "notes": "Runic RS8752XM dual 250 MHz 1 pA CMOS RR-out op-amp, unity-gain stable, Vcm=-0.2..V+-1.1 V, MSOP-8",
        },
    ),
    Part(
        "ref3030", "C38423", "Reference_Voltage:REF3030",
        "Package_TO_SOT_SMD:SOT-23",
        "REF3030AIDBZR", mpn="REF3030AIDBZR", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2410010301_Texas-Instruments-REF3030AIDBZR_C38423.pdf",
            "notes": "TI REF3030 (SBVS032), 3.000 V +/-0.2%, 25 mA max, 50 uA Iq, 1 mV dropout unloaded",
        },
    ),
    Part(
        "lvc2g66", "C282354", "ppa:74LVC2G66",
        "Package_SO:TSSOP-8_3x3mm_P0.65mm",
        "74LVC2G66DP", mpn="74LVC2G66DP,125", basic=False, per_board=1, rotation=90,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2407241028_Nexperia-74LVC2G66DP-125_C282354.pdf",
            "notes": "Nexperia 74LVC2G66DP dual independent 440 MHz 6 ohm SPST analog switch, TSSOP-8 (SOT505-2); 1E=pin7, 2E=pin3",
        },
    ),
    Part(
        "rt9013", "C47773", "ppa:RT9013-33GB",
        "Package_TO_SOT_SMD:SOT-23-5",
        "RT9013-33GB", mpn="RT9013-33GB", basic=False, per_board=2,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_1809081207_Richtek-Tech-RT9013-33GB_C47773.pdf",
            "notes": "Richtek RT9013 (DS9013-10), 500 mA 3.3 V LDO, 30 uVrms noise, 250 mV typ dropout @ 500 mA",
        },
    ),
    Part(
        "srv05_4", "C7420376", "Power_Protection:SRV05-4",
        "Package_TO_SOT_SMD:SOT-23-6",
        "SRV05-4", mpn="SRV05-4", basic=False, per_board=2,
        extra={
            "preferred": True,
            "datasheet": "https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/2407101112_hongjiacheng-SRV05-4_C7420376.pdf",
            "notes": "Hongjiacheng SRV05-4 (Rev 2.2), 4-ch 1 pF 5 V ESD array (Preferred $0 fee); D1 USB-C, D2 4-ch pod",
        },
    ),
    # --- Diodes, FET --------------------------------------------------------------------------
    Part(
        "bav199", "C40919", "ppa:BAV199",
        "Package_TO_SOT_SMD:SOT-23",
        "BAV199", mpn="BAV199,215", basic=False, per_board=2,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2302141530_Nexperia-BAV199-215_C40919.pdf",
            "notes": "Nexperia BAV199 (2020-09-01), 3 pA typ leakage dual series diode: 1=A1, 2=K2, 3=K1_A2",
        },
    ),
    Part(
        "d_1n5819ws", "C191023", "Device:D_Schottky",
        "Diode_SMD:D_SOD-323",
        "1N5819WS", mpn="1N5819WS", basic=True, per_board=1,
        extra={"notes": "Low-Vf Schottky for BOOTSEL diode-OR from BTN_MODE_N to QSPI_SS_N (<0.3 V drop)"},
    ),
    Part(
        "ao3401a", "C15127", "Transistor_FET:AO3401A",
        "Package_TO_SOT_SMD:SOT-23",
        "AO3401A", mpn="AO3401A", basic=True, per_board=1,
        extra={"notes": "30 V 4 A P-MOSFET, 85 mOhm @ Vgs=-2.5 V, LCD backlight PWM switch"},
    ),
    # --- Magnetics, Crystal, Connectors, Switches, Mechanical, Protection ---------------------
    Part(
        "l_3u3", "C42411119", "Device:L",
        "rpi:RP2350_80QFN_minimal__L_pol_2016",
        "3.3uH", mpn="AOTA-B201610S3R3-101-T", basic=False, per_board=1,
        extra={
            "datasheet": "https://abracon.com/datasheets/AOTA-B201610S.pdf",
            "notes": "Abracon 2016 (2.0x1.6x1.0 mm) molded inductor, 115 mOhm, 2.1 A Irms / 2.4 A Isat (RP2350 official)",
        },
    ),
    Part(
        "fb_600r", "C1002", "Device:FerriteBead_Small",
        "Inductor_SMD:L_0603_1608Metric",
        "600R@100MHz", mpn="GZ1608D601TF", basic=True, per_board=1,
        extra={"notes": "200 mA, 450 mOhm DCR, isolates analog +5V rail"},
    ),
    Part(
        "xtal_12m", "C284154", "Device:Crystal_GND24",
        "Crystal:Crystal_SMD_2520-4Pin_2.5x2.0mm",
        "12MHz", mpn="TXM12M0004252DBCEO00T", basic=False, per_board=1,
        extra={
            "datasheet": "https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/1810251532_Yajingxin-TXM12M0004252DBCEO00T_C284154.pdf",
            "notes": "YXC/TXC-style 12.000 MHz 2520, CL=10 pF, ESR<=120 ohm, +/-10 ppm; 15 pF load caps and 1k on XOUT. "
                     "Higher ESR than the 3225 ABM8-272-T3 Raspberry Pi validated: check start-up on first boards",
        },
    ),
    Part(
        "usbc", "C165948", "Connector:USB_C_Receptacle_USB2.0_16P",
        "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
        "USB-C", mpn="TYPE-C-31-M-12", basic=False, per_board=1,
        extra={"notes": "HRO TYPE-C-31-M-12 16-pin USB 2.0 Type-C receptacle"},
    ),
    Part(
        "jst_sh6", "C53454624", "Connector_Generic_MountingPin:Conn_01x06_MountingPin",
        "Connector_JST:JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal",
        "SH1.0-6P", mpn="HC-SH1.0-6PWT", basic=False, per_board=1,
        extra={
            "notes": "HCTL HC-SH1.0-6PWT 6-pin 1.0 mm horizontal right-angle SH SMD header (SM06B-SRSS-TB compatible)",
        },
    ),
    Part(
        "button", "C393942", "ppa:SW_Side_TS24CA",
        "ppa:SW_Push_Side_TS24CA",
        "TS24CA", mpn="TS24CA", basic=False, per_board=4, rotation=0,
        extra={"notes": "SHOU HAN TS24CA 4.7x3.5x1.9 mm pure-SMD right-angle tactile switch (1.6 N, Ask AI / Mode / Up / Down)"},
    ),
    Part(
        "wago_2060_3p", "C2765056", "Connector_Generic:Conn_01x03",
        "ppa:Nose_WAGO2060_KF381_ProbePads",
        "2060-453/998-404", mpn="2060-453/998-404", basic=False, per_board=1, rotation=0,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2106061734_WAGO-2060-453-998-404_C2765056.pdf",
            "notes": "WAGO 2060 3P 4.0 mm SMD spring terminal (320 V / 9 A, 18-24 AWG, 4.5 mm H); footprint also has DNP 3.81 mm KF250NH THT + 14x2.8 mm via-stitched F/B probe pads",
        },
    ),
    Part(
        "ptc_500ma", "C20978", "Device:Polyfuse_Small",
        "Fuse:Fuse_0805_2012Metric",
        "500mA PTC", mpn="SMD0805P050TF", basic=False, per_board=2,
        extra={"notes": "500 mA hold / 1 A trip, 6 V, 0805 resettable PTC fuse (shared on VBUS and side pod +3V3)"},
    ),
    # --- Resistors (all Basic 1% 100 ppm/C or Preferred $0 fee) ------------------------------
    Part("r_470k_0805", "C17709", "Device:R", _R0805, "470k 1%", per_board=4, mpn="0805W8F4703T5E", basic=True,
         extra={"volts": 150, "mw": 125, "notes": "Two in series = 940k, 300 V combined working voltage"}),
    Part("r_51k_0402", "C25794", "Device:R", _R0402, "51k 1%", per_board=2, mpn="0402WGF5102TCE", basic=True),
    Part("r_510r_0402", "C25123", "Device:R", _R0402, "510R 1%", per_board=2, mpn="0402WGF5100TCE", basic=True),
    Part("r_1k5_0402", "C25867", "Device:R", _R0402, "1.5k 1%", per_board=2, mpn="0402WGF1501TCE", basic=True),
    Part("r_2k_0402", "C4109", "Device:R", _R0402, "2k 1%", per_board=1, mpn="0402WGF2001TCE", basic=True),
    Part("r_0r_0603", "C21189", "Device:R", _R0603, "0R", per_board=1, mpn="0603WAF0000T5E", basic=True),
    Part("r_27r_0603", "C25190", "Device:R", _R0603, "27R", per_board=3, mpn="0603WAF270JT5E", basic=False,
         extra={"preferred": True}),
    Part("r_33r_0402", "C25105", "Device:R", _R0402, "33R", per_board=8, mpn="0402WGF330JTCE", basic=True),
    Part("r_330r_0402", "C25104", "Device:R", _R0402, "330R 1%", per_board=2, mpn="0402WGF3300TCE", basic=True),
    Part("r_220r_0402", "C25091", "Device:R", _R0402, "220R", per_board=6, mpn="0402WGF2200TCE", basic=True),
    Part("r_1k_0402", "C11702", "Device:R", _R0402, "1k", per_board=3, mpn="0402WGF1001TCE", basic=True),
    Part("r_5k1_0402", "C25905", "Device:R", _R0402, "5.1k", per_board=2, mpn="0402WGF5101TCE", basic=True),
    Part("r_10k_0402", "C25744", "Device:R", _R0402, "10k", per_board=3, mpn="0402WGF1002TCE", basic=True),
    Part("r_47k_0402", "C25792", "Device:R", _R0402, "47k", per_board=1, mpn="0402WGF4702TCE", basic=True),
    Part("r_100k_0402", "C25741", "Device:R", _R0402, "100k", per_board=4, mpn="0402WGF1003TCE", basic=True),
    # --- Capacitors (Basic or Preferred $0 fee) -----------------------------------------------
    Part("c_1p_0402", "C1550", "Device:C", _C0402, "1pF C0G", per_board=2, mpn="0402CG1R0C500NT", basic=False,
         extra={"preferred": True, "notes": "50 V rated C0G across R1+R2"}),
    Part("c_4p7_0402", "C1569", "Device:C", _C0402, "4.7pF C0G", per_board=2, mpn="0402CG4R7C500NT", basic=False,
         extra={"preferred": True}),
    Part("c_22p_0402", "C1555", "Device:C", _C0402, "22pF C0G", per_board=2, mpn="0402CG220J500NT", basic=True),
    Part("c_15p_0402", "C1548", "Device:C", _C0402, "15pF C0G", per_board=2, mpn="0402CG150J500NT", basic=True),
    Part("c_180p_0402", "C78636", "Device:C", _C0402, "180pF C0G", per_board=2, mpn="0402CG181J500NT", basic=False),
    Part("c_10n_0402", "C15195", "Device:C", _C0402, "10nF", per_board=2, mpn="CL05B103KB5NNNC", basic=True),
    Part("c_100n_0402", "C1525", "Device:C", _C0402, "100nF", per_board=20, mpn="CL05B104KO5NNNC", basic=True),
    Part("c_1u_0402", "C52923", "Device:C", _C0402, "1uF", per_board=5, mpn="CL05A105KA5NQNC", basic=True),
    Part("c_4u7_0402", "C23733", "Device:C", _C0402, "4.7uF", per_board=3, mpn="CL05A475MP5NRNC", basic=True,
         extra={"notes": "Samsung 4.7uF 10V X5R 0402 Basic (compact RP2354B buck decoupling)"}),
    Part("c_10u_0603", "C19702", "Device:C", _C0603, "10uF", per_board=4, mpn="CL10A106KP8NNNC", basic=True),
]

PARTS: dict[str, Part] = {p.key: p for p in _LIST}
