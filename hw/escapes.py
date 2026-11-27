"""Fixed pad escapes laid before any planned routing, for pads that the planned analog copper would otherwise box in.

The gain-switch control pins of U6 (74LVC2G66) sit between its analog pins, and the planned CH1/CH2 output and
switch tracks wrap the package; without a reserved escape, nothing can reach those two pins. Each gets a short
top-layer stub to a via under the package body; the router joins the via from the bottom layer.
Coordinates are board-datum mm (as PLACE in hw/gen_pcb.py). Drawn and locked by hw/route.py --tracks-only and
seen as fixed copper by hw/tools/plan_routes.py.
"""

_U6 = (59.8, 0.0)          # PLACE["U6"]; its pins 3 / 7 are at x = -0.33 / +0.32, y = -2.15 / +2.15

TRACKS = [   # (net, layer, width, x1, y1, x2, y2)
    ("CH2_GAIN_LO", "F", 0.15, _U6[0] - 0.33, -2.15, _U6[0] - 0.33, -1.05),
    ("CH1_GAIN_LO", "F", 0.15, _U6[0] + 0.32, 2.15, _U6[0] + 0.32, 1.05),
]
VIAS = [     # (net, x, y)
    ("CH2_GAIN_LO", _U6[0] - 0.33, -1.05),
    ("CH1_GAIN_LO", _U6[0] + 0.32, 1.05),
]

# RP2354B south pins 53..57 (0.4 mm pitch, pad ends at y = +5.35): two staggered via rows in the free patch
# between C28 (x 94.7) and the ADC_AVDD track to C29 (x 97.9). Via centres keep 0.35 mm from other nets' copper
# (0.10 drill radius + 0.25 hole clearance); pins 54..56 pass between the two first-row vias to the second row.
_U1 = (94.5, 0.0)          # DATUM["rp2354b_xy"]
_SOUTH = [   # (net, pin x, waypoints..., via): the stub runs pin -> waypoints -> via on F.Cu
    ("CH2_DC_SENSE", 95.5, [], (95.40, 5.85)),
    ("VBUS_SENSE", 97.1, [], (97.35, 5.85)),
    ("BTN_DOWN_N", 95.9, [(95.9, 6.10)], (95.70, 6.65)),     # straight down past the first-row via, then over
    ("BTN_UP_N", 96.3, [], (96.30, 6.65)),
    ("BTN_MODE_N", 96.7, [], (96.90, 6.65)),
]
for _net, _px, _way, _via in _SOUTH:
    _pts = [(_px, 4.90), (_px, 5.35)] + _way + [_via]
    for (_x1, _y1), (_x2, _y2) in zip(_pts, _pts[1:]):
        TRACKS.append((_net, "F", 0.15, _x1, _y1, _x2, _y2))
    VIAS.append((_net, *_via))

# AD9288 (U2, LQFP-48, no exposed pad) mode pins S1 / S2 face the analog front end, whose planned tracks wrap that
# side of the package: each gets a stub inward past its pad to a via under the package body.
for _net, _py, _vy in (("ADC_S1", 0.75, 0.55), ("ADC_S2", 1.25, 1.45)):
    TRACKS.append((_net, "F", 0.15, 66.84, _py, 68.35, _vy))
    VIAS.append((_net, 68.35, _vy))

# CH2 DC-sense divider R24B / C41B sit in the front end, where the planned CH2_OUT track wraps them: a stub from
# R24B pad 2 to a via just north of it, clear of the CH2 test pad.
TRACKS.append(("CH2_DC_SENSE", "F", 0.15, 64.21, 7.50, 64.70, 6.60))
VIAS.append(("CH2_DC_SENSE", 64.70, 6.60))

# ADC analog ground returns. C46 / C47 (AVDD decoupling north and south of the ADC) have their AGND pad facing the
# ADC's west AGND pins 1 / 12; C49 (east AVDD decoupling) joins AGND pin 32 and the AGND-GND tie R16 beside it.
TRACKS += [
    ("AGND", "F", 0.25, 66.42, -5.60, 66.42, -2.75),
    ("AGND", "F", 0.25, 66.42, 5.60, 66.42, 2.75),
    ("AGND", "F", 0.25, 75.16, -0.75, 77.50, -0.98),
    ("AGND", "F", 0.25, 77.50, -0.98, 78.725, -0.98),
]
