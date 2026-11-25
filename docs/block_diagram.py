"""docs/img/block-diagram.svg: analog front end, star-ground split, and RP2354B digital core.

    .venv/bin/python docs/block_diagram.py
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "img" / "block-diagram.svg"
W, H = 960, 520
BARRIER = 390
AFE_BG, DIG_BG = "#fdecea", "#e8f0fb"
AFE_BOX, BRIDGE_BOX, DIG_BOX, PWR_BOX = "#f6c9c4", "#e9e1f7", "#c9dcf5", "#d8eed8"
INK = "#1d2330"

parts: list[str] = []


def box(x: float, y: float, w: float, h: float, lines: list[str], fill: str) -> None:
    parts.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
        f'fill="{fill}" stroke="{INK}" stroke-width="1.3"/>'
    )
    y0 = y + h / 2 - (len(lines) - 1) * 8
    for i, line in enumerate(lines):
        weight = "600" if i == 0 else "400"
        fsize = "12.5" if i == 0 else "11.5"
        parts.append(
            f'<text x="{x + w / 2}" y="{y0 + i * 16 + 4}" text-anchor="middle" '
            f'font-weight="{weight}" font-size="{fsize}">{line}</text>'
        )


def arrow(points: list[tuple[float, float]], label: str = "", at: tuple[float, float] | None = None, both: bool = False) -> None:
    pts = " ".join(f"{x},{y}" for x, y in points)
    start = ' marker-start="url(#a0)"' if both else ""
    parts.append(
        f'<polyline points="{pts}" fill="none" stroke="{INK}" stroke-width="1.5" '
        f'marker-end="url(#a1)"{start}/>'
    )
    if label:
        lx, ly = at or ((points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2 - 6)
        parts.append(
            f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="11" fill="#3e4656">{label}</text>'
        )


def main() -> None:
    parts.clear()
    parts.append(f'<rect width="{BARRIER}" height="{H}" fill="{AFE_BG}"/>')
    parts.append(f'<rect x="{BARRIER}" width="{W - BARRIER}" height="{H}" fill="{DIG_BG}"/>')
    parts.append('<text x="14" y="22" font-weight="700" fill="#a3261b">ANALOG FRONT END (AGND island + AFE keepout)</text>')
    parts.append(f'<text x="{W - 14}" y="22" text-anchor="end" font-weight="700" fill="#1f4e99">DIGITAL CORE (GND plane)</text>')
    parts.append(f'<line x1="{BARRIER}" y1="30" x2="{BARRIER}" y2="{H - 12}" stroke="#a3261b" stroke-width="2" stroke-dasharray="7 5"/>')
    parts.append(f'<text x="{BARRIER - 8}" y="{H - 16}" text-anchor="end" font-size="11.5" fill="#a3261b">R16 0 Ω Star GND</text>')

    # Probe front-end chain (left -> bridge)
    box(16, 48, 108, 66, ["J2 M3 Nose", "Stainless probe", "±25 V max input"], AFE_BOX)
    box(144, 48, 114, 66, ["1 MΩ Divider", "R1-R3 + BAV199", "1pF C0G comp."], AFE_BOX)
    box(276, 48, 106, 66, ["U4 + U6 AFE", "OPA356 buffer", "RS2227 1×/10×"], AFE_BOX)
    arrow([(124, 81), (144, 81)])
    arrow([(258, 81), (276, 81)])

    # ADC & Comparator spanning the bridge
    box(336, 152, 134, 72, ["U2 MS9280", "10-bit 35 MSPS", "1.0..2.0 V ADC"], BRIDGE_BOX)
    box(156, 158, 128, 64, ["U7 TLV3501", "4.5 ns comparator", "0..3 V DAC thr"], AFE_BOX)
    arrow([(329, 114), (329, 134), (403, 134), (403, 152)], "V_ADC", at=(365, 130))
    arrow([(220, 114), (220, 158)], "V_BUF", at=(244, 140))

    # Analog rails + star reference
    box(16, 268, 122, 68, ["Analog Power", "U10 RT9013 +3V3", "U11/U12 -1V8"], PWR_BOX)
    box(156, 268, 128, 68, ["U5 REF3030", "+3.000 V ref", "1.00 / 0.25 V"], PWR_BOX)
    arrow([(284, 302), (316, 302), (316, 114)], "bias", at=(299, 250))

    # Digital core (RP2354B)
    box(520, 130, 224, 130, ["U1 RP2354B (QFN-80)", "Dual Cortex-M33 @ 150 MHz", "PIO0: 10-bit scope capture", "PIO1: 4-ch pod + trig", "1.1 V buck, 12 MHz XTAL"], DIG_BOX)
    arrow([(470, 188), (520, 188)], "D0..9 + CLK (GPIO0..12)")
    arrow([(284, 190), (312, 190), (312, 242), (520, 242)], "COMP_OUT (GPIO16)", at=(430, 236))
    arrow([(520, 256), (220, 256), (220, 222)], "THR_PWM (GPIO32)", at=(305, 251))

    # USB-C + Digital power
    box(792, 48, 148, 68, ["J1 USB-C + D1 ESD", "F1 500 mA PTC", "CC1/CC2 5.1 kΩ"], PWR_BOX)
    box(610, 48, 148, 68, ["U9 RT9013-3.3", "+3V3_DIG rail", "SWD / RUN / BOOT"], PWR_BOX)
    arrow([(792, 82), (758, 82)], "VBUS")
    arrow([(866, 116), (866, 152), (744, 152)], "USB D+/D-", at=(812, 146))
    arrow([(684, 116), (684, 130)], "3.3 V")

    # Interfaces below RP2354B
    box(436, 326, 158, 66, ["J4 Molex 8P FPC", "0.96\" IPS 80x160", "SPI0 + AO3401A BL"], DIG_BOX)
    box(614, 326, 152, 66, ["SW1 / SW2 + LEDs", "Ask-AI, Mode/BOOT", "2x WS2812B-2020"], DIG_BOX)
    box(786, 326, 154, 66, ["J3 JST-SH 6P Pod", "U8 SN74LV4T125", "D2 SRV05-4 + PTC"], DIG_BOX)

    arrow([(560, 260), (560, 296), (515, 296), (515, 326)], "GPIO21..26", at=(515, 290))
    arrow([(662, 260), (662, 326)], "GPIO28..30", at=(704, 298))
    arrow([(720, 260), (720, 296), (863, 296), (863, 326)], "GPIO17..20 (PIO1)", at=(818, 290))

    # Firmware + Case strip
    box(16, 424, 356, 56, ["Calibration & Safety", "2-pt cal compensates 100 ppm Basic R", "USB-GND referenced (max ±25 V peak)"], AFE_BOX)
    box(436, 424, 504, 56, ["C11 Firmware + USB CDC + build123d Pen Shell", "Zero-CPU PIO DMA, trigger/FFT metrics, 1.7 mm M2 self-tapping case"], DIG_BOX)

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="{INK}">\n'
        '<defs>'
        f'<marker id="a1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>'
        f'<marker id="a0" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M10,0 L0,5 L10,10 z" fill="{INK}"/></marker>'
        '</defs>\n'
        + "\n".join(parts)
        + "\n</svg>\n"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg, encoding="utf8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
