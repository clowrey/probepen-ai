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
    parts.append(f'<text x="{BARRIER - 8}" y="{H - 16}" text-anchor="end" font-size="11.5" fill="#a3261b">R16 0 Ω AGND–GND bridge at the ADC</text>')

    # Probe front end, one chain per channel (both drawn as one)
    box(16, 48, 108, 66, ["J5 WAGO 2060", "CH1 · AGND · CH2", "push-in nose"], AFE_BOX)
    box(144, 48, 114, 66, ["940k / 1M divider", "BAV199 clamp", "CT1/CT2 comp."], AFE_BOX)
    box(276, 48, 106, 66, ["U3 RS8752", "follower ×2", "rest = REF_MID"], AFE_BOX)
    arrow([(124, 81), (144, 81)])
    arrow([(258, 81), (276, 81)])
    box(276, 152, 106, 72, ["U4 RS8752", "gain stage ×2", "U6 74LVC2G66", "range switch"], AFE_BOX)
    arrow([(329, 114), (329, 152)])

    # Analog rails and reference
    box(16, 268, 122, 68, ["U10 RT9013", "+3V3_ANA", "FB1 from +5 V"], PWR_BOX)
    box(156, 268, 128, 68, ["U5 REF3030", "+3.000 V ref", "REF_MID, ADC_AVDD"], PWR_BOX)
    arrow([(284, 302), (316, 302), (316, 224)], "REF_MID", at=(345, 262))

    # ADC on the boundary
    box(420, 152, 134, 72, ["U2 AD9288", "2 × 8-bit 80 MSPS", "1.024 Vp-p single-ended"], BRIDGE_BOX)
    arrow([(382, 188), (420, 188)], "CHx", at=(401, 182))

    # Digital core (RP2354B)
    box(610, 130, 200, 130, ["U1 RP2354B (QFN-80)", "2 × Cortex-M33, 150 MHz", "PIO0: both ADC buses", "PIO1: 4-ch pod", "1.1 V buck, 12 MHz XTAL"], DIG_BOX)
    arrow([(554, 176), (610, 176)], "A: GPIO16-23", at=(582, 170))
    arrow([(554, 200), (610, 200)], "B: GPIO32-39", at=(582, 214))
    arrow([(610, 244), (487, 244), (487, 224)], "ADC_CLK GPIO24", at=(548, 258))
    arrow([(382, 214), (400, 214), (400, 290), (700, 290), (700, 260)], "DC sense (ADC0/2) · range (GPIO10/13)", at=(560, 284), both=True)

    # USB-C + digital power
    box(820, 48, 128, 68, ["J1 USB-C", "F1 PTC, D1 ESD", "CC 5.1 kΩ"], PWR_BOX)
    box(640, 48, 140, 68, ["U9 RT9013", "+3V3_DIG", "VBUS sense (ADC6)"], PWR_BOX)
    arrow([(820, 82), (780, 82)], "VBUS")
    arrow([(884, 116), (884, 160), (810, 160)], "USB FS", at=(850, 154))
    arrow([(710, 116), (710, 130)], "3.3 V")

    # Interfaces below RP2354B
    box(436, 340, 158, 66, ["J4 LCD pads (bottom)", "1.14\" IPS 240×135", "SPI1 + AO3401A BL"], DIG_BOX)
    box(614, 340, 152, 66, ["SW1-SW4 side buttons", "ASK · MODE/BOOT", "UP · DOWN"], DIG_BOX)
    box(786, 340, 162, 66, ["J3 JST-SH 6P pod", "220R + SRV05-4", "PTC on the 3V3 pin"], DIG_BOX)
    arrow([(640, 260), (640, 316), (515, 316), (515, 340)], "GPIO25-31", at=(560, 310))
    arrow([(690, 260), (690, 340)], "GPIO5, 43-45", at=(738, 330))
    arrow([(780, 260), (780, 316), (867, 316), (867, 340)], "GPIO0-3", at=(840, 310))

    # Firmware + case strip
    box(16, 424, 356, 56, ["Ranges ±26 V and ±5 V, USB-ground referenced", "LED1/LED2 front work-lights on the nose"], AFE_BOX)
    box(436, 424, 512, 56, ["C11 firmware: PIO + DMA capture, trigger, auto-range, LCD UI, USB CDC", "build123d two-piece pen case, M2 screws self-tap into the plastic"], DIG_BOX)

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
