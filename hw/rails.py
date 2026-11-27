"""Power distribution on In2.Cu and under the MCU.

Both inner layers are ground (GND, with AGND under the analog front end). In1.Cu carries nothing else.
In2.Cu also carries the supply rails below as wide strips, kept to corridors along the board, so the
outer layers stay free for signals. Pads reach a rail through a short stub and a via.

Coordinates: board-datum mm, KiCad Y-down. Each rail is a list of rectangles (x0, y0, x1, y1).
"""
from hardware.pcb_datum import PCB as _DATUM

_ADC_X = float(_DATUM["ad9288_xy"][0])
_MCU_X, _MCU_Y = (float(v) for v in _DATUM["rp2354b_xy"])

# No rail runs beside the MCU's north or south pin rows (x within about 6 mm of its centre): those strips
# are where the pins' escape vias go. The 3.3 V rails stop at the B.Cu pour under the MCU, which bridges
# them (TIE_VIAS); the 5 V and reference rails pass as narrow tracks at the board edge.
IN2_RAILS = {
    # digital 3.3 V: a rail along each long edge, tied by one cross-bar tail-side of the LCD flex pads,
    # and two fingers under the ADC body for its digital supply pins 28 / 33
    "+3V3_DIG": [(66.0, 6.0, _MCU_X - 5.2, 7.0), (107.0, 6.0, 119.0, 7.0), (107.0, -7.0, 108.0, 7.0),
                 (_ADC_X - 2.2, -7.0, _MCU_X - 5.2, -6.0), (_MCU_X + 6.0, -7.0, 108.0, -6.0), (81.0, -7.0, 82.0, 7.0),
                 (_ADC_X + 2.2, -2.9, 81.0, -1.5), (_ADC_X + 2.2, 1.5, 81.0, 2.9)],
    # analog 3.3 V: north rail from the LDO, a centre rail under the gain-stage ICs, a rail over the
    # button row (south of the analog ground island, so the island's pads can drop vias to AGND), cross-bars at the clamps and at the LDO, and a finger to the ADC's analog supply pins
    "+3V3_ANA": [(26.8, -7.0, _ADC_X - 3.0, -6.0), (26.8, 4.7, _ADC_X + 0.4, 5.5), (37.5, -7.0, 39.3, 5.5),
                 (26.8, -7.0, 27.5, 6.8), (26.8, -3.1, 29.3, -2.1), (26.8, 5.8, 29.3, 6.8), (37.5, -0.6, 61.5, 0.6),
                 (_ADC_X - 0.4, -0.6, _ADC_X + 7.8, 0.6), (_ADC_X - 0.4, -0.6, _ADC_X + 0.4, 5.5)],
    # 5 V from the USB end to the analog LDO's ferrite, along the north edge, with branches across the
    # tail and to the side header's ESD array
    "+5V_VBUS": [(39.0, -8.35, _MCU_X - 6.0, -7.45), (_MCU_X - 6.5, -8.5, _MCU_X + 7.0, -8.1),
                 (_MCU_X + 6.5, -8.35, 127.5, -7.45), (123.6, -8.35, 124.4, 8.35), (116.0, 7.45, 127.5, 8.35),
                 (116.5, -8.35, 117.5, -1.0)],
    # 3.0 V reference to the MCU's ADC supply filter (R23): a quiet DC rail along the south edge
    "+3V0_REF": [(45.0, 7.4, _MCU_X - 6.0, 8.2), (_MCU_X - 6.5, 8.0, _MCU_X + 5.9, 8.4), (45.0, 6.0, 45.8, 8.2)],
}
# pads of rail nets that are not supply nets everywhere: only these get a via to the rail
EXTRA_STUBS = [("+3V0_REF", "TP_3V0R"), ("+3V0_REF", "R23")]

# +3V3_DIG pour on B.Cu under the MCU and its decoupling rows (CellKeeper feeds the IOVDD pins this way,
# through vias inside the pad ring). It overlaps the south +3V3_DIG rail, where TIE_VIAS join the two.
B_POUR_3V3 = (_MCU_X - 6.2, -8.3, _MCU_X + 7.0, 8.3)
TIE_VIAS = [("+3V3_DIG", _MCU_X - 5.7, 6.5), ("+3V3_DIG", _MCU_X - 5.7, -6.5), ("+3V3_DIG", _MCU_X + 6.5, -6.5)]


def segments():
    """The rails as In2.Cu tracks: (net, "In2", width, x1, y1, x2, y2). Each rectangle is one track as wide as
    the rectangle is narrow. Tracks, not pours: the autorouter keeps other nets' vias off a track, while a
    via through a poured strip only shows up later as a cut rail."""
    out = []
    for net, rects in IN2_RAILS.items():
        for x0, y0, x1, y1 in rects:
            w = round(min(x1 - x0, y1 - y0), 3)
            if x1 - x0 >= y1 - y0:
                out.append((net, "In2", w, x0 + w / 2, (y0 + y1) / 2, x1 - w / 2, (y0 + y1) / 2))
            else:
                out.append((net, "In2", w, (x0 + x1) / 2, y0 + w / 2, (x0 + x1) / 2, y1 - w / 2))
    return out
