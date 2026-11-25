"""Part catalogue: one entry per distinct orderable part.

Every LCSC code, library type (Basic vs Extended), package, and datasheet reference was verified
against JLCPCB's live catalog and official component datasheets. `per_board` is enforced against
`hw/design.py` by `tests/test_design.py`.
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
        "Package_DFN_QFN:QFN-80-1EP_10x10mm_P0.4mm_EP3.4x3.4mm_ThermalVias",
        "RP2354B", mpn="RP2354B", basic=False, per_board=1,
        extra={
            "datasheet": "https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf",
            "notes": "QFN-80 S14.8, 2 MB internal QSPI flash, 150 MHz max clk_sys, ADC_AVDD >= 2.97 V, VREG_AVDD 3.135-3.63 V",
        },
    ),
    Part(
        "ms9280", "C1509021", "ppa:MS9280",
        "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm",
        "MS9280", mpn="MS9280", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2310071001_Hangzhou-Ruimeng-Tech-MS9280_C1509021.pdf",
            "notes": "Hangzhou Ruimeng MS9280 V1.3, 10-bit 35 MSPS, 2.7-5.5 V; low stock (5 pcs); pins 5-12 drop-in for AD9280ARSZRL (C130297)",
        },
    ),
    Part(
        "opa356", "C183100", "Amplifier_Operational:OPA356xxDBV",
        "Package_TO_SOT_SMD:SOT-23-5",
        "OPA356AIDBVR", mpn="OPA356AIDBVR", basic=False, per_board=2,
        extra={
            "datasheet": "https://www.ti.com/lit/ds/symlink/opa356.pdf",
            "notes": "TI OPA356 (SBOS222), 200 MHz CMOS RR-out, 3 pA bias, 2.5-5.5 V",
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
        "rs2227", "C255478", "ppa:RS2227XN",
        "Package_SO:MSOP-10_3x3mm_P0.5mm",
        "RS2227XN", mpn="RS2227XN", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2202251830_Jiangsu-RUNIC-Tech-RS2227XN_C255478.pdf",
            "notes": "Runic RS2227 Rev B.1, DPDT 550 MHz, 1.8-5.5 V, Ron ~6 ohm typ, Con 7 pF",
        },
    ),
    Part(
        "tlv3501", "C193413", "Comparator:TLV3501AIDBV",
        "Package_TO_SOT_SMD:SOT-23-6",
        "TLV3501AIDBVR", mpn="TLV3501AIDBVR", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.ti.com/lit/ds/symlink/tlv3501.pdf",
            "notes": "TI TLV3501 (SBOS321E), 4.5 ns RR comparator, 2.7-5.5 V, pin 5=OUT, pin 6=SHDN",
        },
    ),
    Part(
        "lv4t125", "C90755", "ppa:SN74LV4T125",
        "Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
        "SN74LV4T125PWR", mpn="SN74LV4T125PWR", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.ti.com/lit/ds/symlink/sn74lv4t125.pdf",
            "notes": "TI SN74LV4T125 (SCLS749C), 4-ch 5 V-tolerant level-shifting buffer, 1.6-5.5 V",
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
        "lm2776", "C69527", "Regulator_SwitchedCapacitor:LM2776",
        "Package_TO_SOT_SMD:SOT-23-6",
        "LM2776DBVR", mpn="LM2776DBVR", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.ti.com/lit/ds/symlink/lm2776.pdf",
            "notes": "TI LM2776 (SNVS956B), 200 mA switched-capacitor inverter, 2.7-5.5 V in -> -VIN out",
        },
    ),
    Part(
        "tps72301", "C34678", "Regulator_Linear:TPS72301DBV",
        "Package_TO_SOT_SMD:SOT-23-5",
        "TPS72301DBVR", mpn="TPS72301DBVR", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2410121941_Texas-Instruments-TPS72301DBVR_C34678.pdf",
            "notes": "TI TPS72301 (SLVS346C), 200 mA negative LDO, Vref=-1.186 V, bipolar EN (active >+1.5 V or <-1.5 V)",
        },
    ),
    Part(
        "usblc6", "C7519", "Power_Protection:USBLC6-2SC6",
        "Package_TO_SOT_SMD:SOT-23-6",
        "USBLC6-2SC6", mpn="USBLC6-2SC6", basic=False, per_board=3,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2410121836_STMicroelectronics-USBLC6-2SC6_C7519.pdf",
            "notes": "ST USBLC6-2 (Doc ID 11265 Rev 5), 3.5 pF max ESD protection",
        },
    ),
    # --- Diodes, FET, LEDs --------------------------------------------------------------------
    Part(
        "bav199", "C40919", "ppa:BAV199",
        "Package_TO_SOT_SMD:SOT-23",
        "BAV199", mpn="BAV199,215", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2302141530_Nexperia-BAV199-215_C40919.pdf",
            "notes": "Nexperia BAV199 (2020-09-01), 3 pA typ leakage dual series diode: 1=A1, 2=K2, 3=K1_A2",
        },
    ),
    Part(
        "d_1n4148ws", "C2128", "Diode:1N4148WS",
        "Diode_SMD:D_SOD-323",
        "1N4148WS", mpn="1N4148WS", basic=True, per_board=1,
        extra={"notes": "Drops +5V_VBUS to ~4.3 V LED_VDD for WS2812B 3.3 V DIN threshold margin"},
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
    Part(
        "ws2812b", "C52917434", "LED:WS2812B-2020",
        "LED_SMD:LED_WS2812B-2020_PLCC4_2.0x2.0mm",
        "WS2812B-2020-V6", mpn="WS2812B-2020-V6", basic=False, per_board=2,
        extra={"notes": "Worldsemi WS2812B-2020-V6, 3.3-5.5 V, VIH=0.6*VDD; 1=DOUT, 2=VSS, 3=DIN, 4=VDD"},
    ),
    # --- Magnetics, Crystal, Connectors, Switches, Mechanical, Protection ---------------------
    Part(
        "l_3u3", "C48020", "Device:L",
        "Inductor_SMD:L_Sunlord_SWPA3015S",
        "3.3uH", mpn="SWPA3015S3R3MT", basic=False, per_board=1,
        extra={"notes": "Sunlord 3x3x1.5 mm shielded power inductor, Isat=1.81 A, Irms=1.36 A, DCR=104 mOhm"},
    ),
    Part(
        "fb_600r", "C1002", "Device:FerriteBead_Small",
        "Inductor_SMD:L_0603_1608Metric",
        "600R@100MHz", mpn="GZ1608D601TF", basic=True, per_board=1,
        extra={"notes": "200 mA, 450 mOhm DCR, isolates analog +5V rail"},
    ),
    Part(
        "xtal_12m", "C20625731", "Device:Crystal_GND24",
        "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
        "12MHz", mpn="ABM8-272-T3", basic=False, per_board=1,
        extra={"notes": "Abracon ABM8-272-T3 (RP2350 hardware guide crystal: 12.000 MHz, CL=10 pF, ESR<=50 ohm, +/-30 ppm)"},
    ),
    Part(
        "fpc_8p", "C293338", "Connector_Generic_MountingPin:Conn_01x08_MountingPin",
        "ppa:Molex_503480-0800_1x08-1MP_P0.50mm_Horizontal",
        "503480-0800", mpn="5034800800", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2406281007_MOLEX-5034800800_C293338.pdf",
            "notes": "Molex 503480-0800 (SD-503480-001), 8P 0.5 mm BackFlip dual-contact FPC socket for FP-096H09A",
        },
    ),
    Part(
        "usbc", "C165948", "Connector:USB_C_Receptacle_USB2.0_16P",
        "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
        "USB-C", mpn="TYPE-C-31-M-12", basic=False, per_board=1,
        extra={"notes": "HRO TYPE-C-31-M-12 16-pin USB 2.0 Type-C receptacle"},
    ),
    Part(
        "jst_sh6", "C160405", "Connector_Generic_MountingPin:Conn_01x06_MountingPin",
        "Connector_JST:JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal",
        "SM06B-SRSS-TB", mpn="SM06B-SRSS-TB(LF)(SN)", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2304140030_JST-SM06B-SRSS-TB-LF-SN_C160405.pdf",
            "notes": "JST SH 6-pin 1.0 mm horizontal right-angle SMD header for side logic pod",
        },
    ),
    Part(
        "button", "C318884", "Switch:SW_Push",
        "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A",
        "TS-1187A", mpn="TS-1187A-B-A-B", basic=True, per_board=2,
        extra={"notes": "5.1x5.1 mm low-profile tactile switch (Ask AI and Mode/BOOTSEL)"},
    ),
    Part(
        "m3_boss", "C5199990", "Connector_Generic:Conn_01x01",
        "ppa:Wurth_9774030360R_M3",
        "9774030360R", mpn="9774030360R", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.we-online.com/components/products/datasheet/9774030360R.pdf",
            "notes": "Wurth WA-SMSI SMD M3 brass threaded spacer (3.0 mm H, 6.0 mm OD)",
        },
    ),
    Part(
        "ptc_500ma", "C20978", "Device:Polyfuse_Small",
        "Fuse:Fuse_0805_2012Metric",
        "500mA PTC", mpn="SMD0805P050TF", basic=False, per_board=1,
        extra={"notes": "500 mA hold / 1 A trip, 6 V, 0805 resettable PTC fuse on USB VBUS"},
    ),
    Part(
        "ptc_100ma", "C70048", "Device:Polyfuse_Small",
        "Fuse:Fuse_0603_1608Metric",
        "100mA PTC", mpn="SMD0603-010-24V", basic=False, per_board=1,
        extra={"notes": "100 mA hold / 300 mA trip, 24 V, 0603 resettable PTC fuse on side pod +3V3"},
    ),
    Part(
        "c_trim", "C3273292", "Device:C_Trim",
        "ppa:C_Trimmer_Knowles_JZ100_4.5x3.2mm",
        "2-10pF", mpn="JZ100", basic=False, per_board=1,
        extra={
            "datasheet": "https://www.lcsc.com/datasheet/lcsc_datasheet_2404031010_Knowles-JZ100_C3273292.pdf",
            "notes": "Knowles Voltronics JZ100, 2.0-10.0 pF, 125 V DC, 4.5x3.2 mm SMD trimmer capacitor",
        },
    ),
    # --- Resistors (precision 0.1% thin-film, HV 0805, Basic 0402/0603) -----------------------
    Part("r_470k_0805", "C17709", "Device:R", _R0805, "470k 1%", per_board=2, mpn="0805W8F4703T5E", basic=True,
         extra={"volts": 150, "mw": 125, "notes": "Two in series = 940k, 300 V combined working voltage"}),
    Part("r_39k2_01", "C861369", "Device:R", _R0603, "39.2k 0.1%", per_board=1, mpn="RT0603BRD0739K2L", basic=False),
    Part("r_1k00_01", "C110776", "Device:R", _R0603, "1.00k 0.1%", per_board=1, mpn="RT0603BRD071KL", basic=False),
    Part("r_500r_01", "C6207671", "Device:R", _R0603, "500R 0.1%", per_board=1, mpn="RT0603BRD07500RL", basic=False),
    Part("r_5k00_01", "C2828621", "Device:R", _R0603, "5.00k 0.1%", per_board=1, mpn="AR03BTCX5001", basic=False),
    Part("r_20k0_01", "C723637", "Device:R", _R0603, "20.0k 0.1%", per_board=1, mpn="RT0603BRD0720KL", basic=False),
    Part("r_7k50_01", "C728597", "Device:R", _R0603, "7.50k 0.1%", per_board=1, mpn="RT0603BRD077K5L", basic=False),
    Part("r_2k50_01", "C3013546", "Device:R", _R0603, "2.50k 0.1%", per_board=1, mpn="AR03BTCX2501N", basic=False),
    Part("r_0r_0603", "C21189", "Device:R", _R0603, "0R", per_board=1, mpn="0603WAF0000T5E", basic=True),
    Part("r_27r_0402", "C25100", "Device:R", _R0402, "27R", per_board=3, mpn="0402WGF270JTCE", basic=False),
    Part("r_33r_0402", "C25105", "Device:R", _R0402, "33R", per_board=13, mpn="0402WGF330JTCE", basic=True),
    Part("r_100r_0402", "C25076", "Device:R", _R0402, "100R", per_board=1, mpn="0402WGF1000TCE", basic=True),
    Part("r_220r_0402", "C25091", "Device:R", _R0402, "220R", per_board=5, mpn="0402WGF2200TCE", basic=True),
    Part("r_1k_0402", "C11702", "Device:R", _R0402, "1k", per_board=2, mpn="0402WGF1001TCE", basic=True),
    Part("r_5k1_0402", "C25905", "Device:R", _R0402, "5.1k", per_board=2, mpn="0402WGF5101TCE", basic=True),
    Part("r_10k_0402", "C25744", "Device:R", _R0402, "10k", per_board=10, mpn="0402WGF1002TCE", basic=True),
    Part("r_47k_0402", "C25792", "Device:R", _R0402, "47k", per_board=1, mpn="0402WGF4702TCE", basic=True),
    Part("r_49k9_0603", "C23184", "Device:R", _R0603, "49.9k", per_board=1, mpn="0603WAF4992T5E", basic=True),
    Part("r_100k_0402", "C25741", "Device:R", _R0402, "100k", per_board=3, mpn="0402WGF1003TCE", basic=True),
    # --- Capacitors (Basic C0G/X7R/X5R where available) ---------------------------------------
    Part("c_1p_0603", "C115062", "Device:C", _C0603, "1pF C0G", per_board=1, mpn="CC0603BRNPO9BN1R0", basic=False),
    Part("c_4p7_0402", "C1569", "Device:C", _C0402, "4.7pF C0G", per_board=1, mpn="0402CG4R7C500NT", basic=False),
    Part("c_15p_0402", "C1548", "Device:C", _C0402, "15pF C0G", per_board=3, mpn="0402CG150J500NT", basic=True),
    Part("c_47p_0402", "C1567", "Device:C", _C0402, "47pF C0G", per_board=1, mpn="0402CG470J500NT", basic=True),
    Part("c_10n_0402", "C15195", "Device:C", _C0402, "10nF", per_board=1, mpn="CL05B103KB5NNNC", basic=True),
    Part("c_100n_0402", "C1525", "Device:C", _C0402, "100nF", per_board=39, mpn="CL05B104KO5NNNC", basic=True),
    Part("c_1u_0402", "C52923", "Device:C", _C0402, "1uF", per_board=5, mpn="CL05A105KA5NQNC", basic=True),
    Part("c_4u7_0603", "C19666", "Device:C", _C0603, "4.7uF", per_board=5, mpn="CL10A475KO8NNNC", basic=True),
    Part("c_10u_0603", "C19702", "Device:C", _C0603, "10uF", per_board=6, mpn="CL10A106KP8NNNC", basic=True),
]

PARTS: dict[str, Part] = {p.key: p for p in _LIST}
