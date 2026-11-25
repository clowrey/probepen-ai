"""Routing architecture, netclass track/via rules, plane mapping, and priority order
for ProbePen-AI (132 x 15 mm, 4-layer JLC04161H-7628).

In1.Cu stays unbroken digital GND + isolated AGND reference plane (zero signal tracks).
F.Cu, B.Cu, and In2.Cu carry all signal, high-voltage divider, USB diff-pair, and routed power tracks (including +3V3_DIG and +3V3_ANA).
"""
from __future__ import annotations

from hw import design

# JLCPCB 4-layer JLC04161H-7628 rules (3.5-mil standard trace/space & 0.40/0.20mm via)
DEFAULT_CLR_MM = 0.095
VIA_DIAM_MM = 0.40
VIA_DRILL_MM = 0.20
HV_CLR_MM = 0.43              # satisfies 0.40 mm DRU rule for /PROBE_TIP & /HV_MID
HOLE_CLR_MM = 0.29            # satisfies 0.25 mm hole clearance rule
EDGE_CLR_MM = 0.35            # satisfies 0.30 mm copper-to-edge rule

HV_NETS = {"PROBE_TIP", "HV_MID"}

# Inner-layer solid planes and whether pads inside them drop directly via blind/through vias
PLANE_ZONES: dict[str, tuple[str, list[tuple[float, float]]]] = {
    "GND": ("In1.Cu", [
        (0.45, -7.05), (131.55, -7.05), (131.55, 7.05),
        (83.2, 7.05), (83.2, -5.2), (51.5, -5.2), (51.5, 7.05), (0.45, 7.05),
    ]),
    "AGND": ("In1.Cu", [
        (51.5, -4.8), (82.6, -4.8), (82.6, 7.05), (51.5, 7.05),
    ]),
}


def track_width_mm(net_name: str) -> float:
    """Nominal trace width per net/netclass (mm)."""
    cls = design.NETCLASS.get(net_name, "LV")
    if cls == "POWER":
        return 0.25
    if cls == "USB":
        return 0.20
    if cls == "ANALOG":
        return 0.16 if net_name not in ("PROBE_TIP", "HV_MID") else 0.25
    if cls == "HIGHSPEED":
        return 0.15
    return 0.15


def net_clearance_mm(net_name: str) -> float:
    if net_name in HV_NETS:
        return HV_CLR_MM + 0.03
    return 0.095


def route_priority(net_name: str) -> tuple[int, str]:
    """Lower key routes first: HV nose -> USB -> XTAL/VREG -> short local pins -> long buses -> planes."""
    if net_name in ("PROBE_TIP", "HV_MID", "V_DIV"):
        return (0, net_name)
    if net_name in ("USB_CONN_DP", "USB_CONN_DM", "USB_DP", "USB_DM", "USB_CC1", "USB_CC2", "SWDIO", "SWCLK"):
        return (1, net_name)
    if net_name in ("XIN", "XOUT_RAW", "XOUT_XTAL", "VREG_LX", "VREG_AVDD", "+1V1_CORE"):
        return (2, net_name)
    # Short local 2-4 mm U2<->R26..35 data links BEFORE ADC_CLK (9 mm around U2) and plane vias
    if net_name.startswith("ADC_D") and not net_name.endswith("_MCU"):
        return (2, net_name)
    if net_name == "ADC_CLK":
        return (3, net_name)
    if net_name.endswith(("_RAW", "_PROT")) and net_name.startswith("POD_"):
        return (3, net_name)
    if net_name in (
        "U4_INP", "U4_INN", "U4_FB_1X", "U4_FB_10X", "V_ADC", "ADC_AIN",
        "REF_MID", "REF_1V00", "REF_0V25", "ADC_VREF", "ADC_REFTF", "ADC_REFBF",
        "ADC_MODE", "COMP_INN", "TPS_FB", "LM2776_C1P", "LM2776_C1N", "-3V3_RAW",
        "+5V_ANA_IN", "COMP_THR_MID", "LED1_DIN", "LED_CASCADE", "LED_VDD",
    ):
        return (3, net_name)
    if net_name in PLANE_ZONES:
        return (9, net_name)
    if net_name in ("+3V3_DIG", "+3V3_ANA"):
        return (4, "00_" + net_name)
    if net_name.startswith("LCD_"):
        return (4, "01_" + net_name)
    if net_name == "ADC_CLK_MCU":
        return (5, "00_CLK")
    if net_name.startswith("ADC_D") and net_name.endswith("_MCU"):
        idx = int(net_name[5:-4])
        return (5, f"01_D{9 - idx}")  # D9..D0 top-to-bottom monotonic order
    if net_name.startswith(("ADC_D", "ADC_CLK", "ADC_OTR", "ADC_STBY", "COMP_OUT", "POD_")):
        return (5, "02_" + net_name)
    if net_name in ("+3V0_REF", "-1V8_ANA", "+3V3_POD", "VBUS_RAW", "+5V_VBUS"):
        return (6, net_name)
    return (7, net_name)


ORDERED_NETS: list[str] = sorted(design.NETS.keys(), key=route_priority)
