# Review notes for the agent working on this repo

Owner-requested review, 2026-11-26. Please read this before continuing, act on it in the
generator scripts (`hw/design.py`, tests), and delete each item once it is handled.

## Status 2026-11-27 (branch `relayout`)

Handled since these notes were written:

- Section 1: REF_MID now equals the divider's resting voltage (0.9785 V vs 0.9781 V), the low range
  is about ±26 V (R6x 330R) and the tests check both ranges at the buffer's input limits.
- Section 2: firmware ported to the AD9288 and the current pin map (it cross-compiles; a full Pico
  SDK build is still to do); README and AGENTS.md rewritten; the board is routed with no unconnected
  items (see README status line).
- Section 3: U1 rotated, the core regulator copied from CellKeeper, the 2520 crystal (C284154) with
  15 pF Basic load capacitors placed at pins 30/31.
- Section 4: re-placed and re-routed from scratch; the rules that came out of it are in
  `docs/ROUTING_GUIDE.md`. Two deviations: the DC-sense 10 nF capacitors sit beside their 10k
  resistors at the source (the MCU side has no room), and there are no SWD/RUN test pads (programming
  is over USB).

- Section 2, later the same night: the case follows the tapered board (WAGO nose windows, LCD window
  in the bottom shell, flexure side buttons, M2 x 8 self-tapping screws); the firmware has a real
  main loop and a host-tested UI, and its scaling now matches the front end (inverting gain stage,
  1.024 V span: +-5.0 V and about +-26 V); `hardware/SCHEMATIC_WIRING_SPEC.md` is generated from
  `design.py` (`hw/tools/gen_wiring_md.py`, checked by a test); block diagram, schematic, layer and
  render images regenerated; JLC export refreshed with live prices (every part in stock).

- Owner decisions, 2026-11-27: the TLV3501 logic probe was removed on purpose; anti-aliasing: the ADC input
  RC is now R25A/R25B 51R (preferred part, no Extended fee) with the Basic 100 pF C42/C42B, a 31 MHz
  pole on both ranges, with no layout change.

Nothing in these notes is open any more except bench testing.

## 1. Front-end range on negative inputs (single-supply AFE)

Checked from `hw/design.py`, still true at commit 984339e (2026-11-26). Per channel: 940k from the tip into
`CHx_VDIV`, 100k up to `+3V0_REF`, 51k down to `AGND`, RS8752XM follower, inverting RS8752XM
stage around `REF_1V00` (Rg 510R, Rf 1.5k, or 1.5k || (220R + 6R) with the switch on), AD9288
input single-ended against `REF_1V00` with +-0.512 V of range.

- Divider: 0.978 V at 0 V in, 34.7 mV per volt (1/28.83).
- The follower's input range is -0.2 V to 2.2 V (V+ - 1.1 V) and its output cannot reach ground.

Resulting ranges:

| Range | Comment in design.py | Actual |
|---|---|---|
| High gain (2.941x) | +-5.0 V | about -4.4 V to +5.7 V |
| Low gain (0.385x)  | +-38.4 V | about -27 V to +35 V |

Problems:

1. **Low range clips in the follower, not the ADC.** `CHx_VDIV` reaches 0 V at -28.2 V in, and
   2.2 V at +35.2 V in. The ADC would allow -37.7 V to +39.0 V, so the "+-38.4 V" comment is
   wrong. `test_afe_gain_and_single_supply_math` only checks -27 V and +30 V, so it passes anyway.
2. **High range is off-centre.** The divider rests at 0.978 V but the gain stage pivots on
   1.000 V. The 22 mV difference times 2.941 is a 65 mV offset at the ADC, about 13 % of the
   half-scale. Calibration can remove the offset, not the lost negative range.
3. **The tip is not at 0 V when idle.** A floating tip reads about +1 V, and a grounded tip
   sources about 1 uA into the circuit under test. Document this in the README input section,
   and have the firmware show "open" rather than +1 V if it can tell.

Suggested fixes:

- Rest `CHx_VDIV` near 1.1 V, the middle of the follower's 0 to 2.2 V window, so the low range
  is symmetric. Otherwise state the range as -27 V / +35 V everywhere.
- Make the gain stage's pivot voltage equal to the divider's resting voltage, for example by
  deriving both from the same tap, so 0 V in sits at ADC mid-scale on both ranges.
- Tighten the test: assert the follower input stays inside 0.05 V to 2.2 V at both ends of each
  *claimed* range, and that 0 V in lands within a few codes of ADC mid-scale.
- Probe compensation still relies on a 1 pF +-0.25 pF capacitor with CT1/CT2 unfitted. The
  lower leg needs about 27.8 pF (1 pF x 940k / 33.8k) and has 22 pF plus strays.

## 2. Other open items (state at commit 984339e)

Verified independently at that commit: ERC clean, 0 schematic-parity issues, DRC 0 errors and
0 unconnected on the committed board and on a board regenerated from the scripts, design rules
unchanged. Routing and export are done. Still open:

- `firmware/` does not cross-compile (`test_arm_cortex_m33_cross_compile_all_sources` fails in
  `main.c`). `main.c`, `afe_cal.c`, `ms9280_capture.pio` and `CMakeLists.txt` still target the
  MS9280 and the old pin map. The capture program needs to read 16 bits (both AD9288 channels).
- `README.md`, `AGENTS.md` ("Current state"), `hardware/SCHEMATIC_WIRING_SPEC.md`,
  `docs/block_diagram.py`, `docs/make_images.py` and the README images still describe the old
  MS9280 / 132 x 15 mm / TLV3501 design and "16 unrouted connections".
- The comparator-based logic probe (TLV3501 + threshold DAC) is gone from `design.py`. Confirm
  with the owner that dropping it is intended, and remove it from the docs if so.
- `case/case.py` has not changed since the outline became tapered with side buttons, a WAGO
  nose terminal and a back-mounted LCD. Check that it models them and that its tests cover them.
- Any change from section 1 alters `design.py`, so regenerate schematic, board and JLC exports
  afterwards and re-run the full test suite.

## 3. RP2354B core regulator layout (owner request: rotate U1)

State at commit 984339e, KiCad coordinates: U1 at (124.5, 50.0), rotation 0. The regulator pins
61-65 (`VREG_AVDD`, `VREG_PGND`, `VREG_LX`, `VREG_VIN`, `VREG_FB`) are on the side facing the
-Y board edge. L1 is at (133.0, 46.8), 3.5 mm past the far side of the chip.

Problems:

- `VREG_LX` runs from pad 63 inward under the package, drops through a via to In2.Cu, runs
  5.5 mm on In2.Cu (the +3V3 plane layer), and comes back up through a second via to L1.
  8.3 mm, 2 vias, 0.26 mm wide. The switch node should be a few mm, wide, on F.Cu only, with
  no vias, and must not cut the plane layer.
- The hot loop (C23 on `VREG_VIN` -> LX -> L1 -> C25 -> `VREG_PGND`) spans about 7 mm. C25 and
  the `VREG_FB` sense point are at L1, far from pin 62.
- `+1V1_CORE` totals 53 mm of track with 8 vias.

Requested change: rotate U1 by -90 degrees (clockwise in KiCad) so the pin 61-80 side faces
the USB-C tail (+X), and place L1, C23 (VIN) and C25 (output) directly beside pins 61-65 on
F.Cu, following the Raspberry Pi RP2350 hardware-design reference layout. `VREG_FB` is taken
at C25, and `VREG_PGND` returns to C23/C25 ground on F.Cu before it meets the plane.

Consequences to handle in `hw/design.py` (GPIO map), `hw/gen_pcb.py` and `hw/routes.py`:

- USB_DP/DM (pins 66, 67) then also face J1. Good.
- The side facing the ADC becomes pins 21-40. Move the AD9288 bus from GPIO0..15 to
  GPIO16..31 (pins 16-19 and 20-36) and set the PIO GPIO base to 16, so the 16 data lines stay
  short. Move LCD, buttons and gain controls to GPIO0..15. Update `probepen_pins.h` and
  `test_pio_and_adc_gpio_windows` to match.
- XIN/XOUT (pins 30, 31) land in the middle of that ADC-facing (-X) side. Keep Y1 on that side,
  directly at the pins (see the crystal item below), and route the data bus around it.
- SWD/RUN (33-35) are on the same side; keep their test pads on B.Cu.
- The RP2354B ADC pins (GPIO40..47) move to the +Y side. Keep `CHx_DC_SENSE` away from LX.

Reference layout to copy (owner's proven RP2354B board, same QFN-80 footprint, U at rotation
-90, offsets in mm from the chip centre, KiCad axes):

| Part | Offset (x, y) | Rotation | Notes |
|---|---|---|---|
| Pad 63 `VREG_LX` | (+4.90, +3.00) | | |
| L, 3.3 uH 2016 (polarised) | (+8.23, +3.00) | -90 | straight out from pad 63 at the same y |
| `VREG_AVDD` 4.7 uF | (+6.33, +5.82) | -90 | |
| `VREG_AVDD` 33R | (+7.37, +5.84) | -90 | |
| Crystal 3225 | (-11.87, -0.20) | 180 | opposite pads 30/31 |
| XOUT 1k series | (-6.87, +0.20) | 180 | |

There `VREG_LX` is 4.1 mm long, F.Cu only, no vias, 0.22 mm wide; `VREG_AVDD` is 3.1 mm, F.Cu
only. Match or beat those figures. Observe the inductor's polarity mark as that board does.

Crystal (owner request: smaller parts, on the -X side, close to U1):

- Replace the 3225 crystal (C9002, CL 20 pF, 2 x 33 pF) with a 2520 part. Candidate:
  LCSC C284154, TXM12M0004252DBCEO00T, 12 MHz, CL 10 pF, ESR 120 ohm max, about $0.10, in stock
  at JLC on 2026-11-26 (Extended). Load capacitors become 2 x 15 pF C0G 0402, LCSC C1548
  (0402CG150J500NT, JLC Basic; owner decision: no 0201, Basic parts only for these). Keep the Basic 1k 0402
  series resistor on XOUT.
- Raspberry Pi validated the oscillator only with the ABM8-272-T3 (3225, 50 ohm ESR). A 120 ohm
  2520 crystal has less start-up margin: take ESR, CL and maximum drive level from the real
  datasheet, record them in `hw/parts.py`, and note in the README that start-up must be checked
  on the first boards (the series resistor may need to drop).
- If that margin is not acceptable, the alternative is a 2016 CMOS oscillator driving XIN
  (for example LCSC C2901608, 12 MHz, 1.62-3.63 V): one 100 nF capacitor, no load capacitors or
  series resistor, XOUT left open, at about $0.41 and a few mA.
- Place the crystal within about 2 mm of pads 30/31 with its capacitors beside it, XIN and
  XOUT on F.Cu only, no vias, a ground guard around them, and no ADC data line underneath on
  In2.Cu.

## 4. Analog routing trace (board at commit 984339e)

Measured from `hw/probepen_ai.kicad_pcb` geometry. x is mm from the nose edge. Fix these in
`hw/gen_pcb.py` (placement, zones) and `hw/routes.py`, most important first.

1. **Op-amp summing nodes are far too long.** `CH1_INV` is 20.8 mm with 4 vias (14.9 mm on
   B.Cu); `CH2_INV` is 30.8 mm with 2 vias. This is the inverting input of a 250 MHz op-amp.
   Place R4x, R5x, C40x and R6x at U4's pins and U6 directly beside them so each `CHx_INV` is
   under about 3 mm, F.Cu only, no vias, and equal on both channels.
2. **The high-impedance divider node is long and partly over digital planes.** `CH1_VDIV` is
   21.5 mm and `CH2_VDIV` 26.2 mm. The plane keepout ends at x = 26.2 but the AGND / +3V3_ANA
   islands only start at x = 31, so 9.7 mm (CH1) and 12.6 mm (CH2) of this node run over GND
   and +3V3_DIG. Shorten the node to a few mm per channel, make both channels the same, and
   make the AGND and +3V3_ANA islands start at the keepout edge so no digital plane lies under
   R3x, R3Ux, C33x, D4x or U3.
3. **The digital LDO is in the analog section.** U9 (+3V3_DIG) sits at x = 35, beside the input
   buffer U3, 100 mm from the USB connector and the MCU. `+5V_VBUS` reaches it over 154 mm of
   0.18-0.28 mm track, 76 mm of it on In2.Cu, and the MCU's supply and return currents then
   travel the length of the board past the AGND island. Move U9 and its capacitors to the tail
   near J1 / U1. Only U10, FB1 and U5 belong at the analog end.
4. **`REF_1V00` is under-decoupled and shared.** It is a 667 ohm divider tap, 59.8 mm long with
   4 vias (12.9 mm on In2.Cu), with one 1 uF (C36). C36 is 5.4 mm from the ADC's ~AINA/~AINB
   pins and 11.4 / 15.6 mm from U4 pins 5 / 3. Both ADC inverting inputs and both gain-stage
   pivots share it, so ADC sampling kickback couples between channels. Add 100 nF at U2 pin 3,
   at U2 pin 10 and at U4 pins 3/5, and keep the net off In2.Cu.
5. **`+3V0_REF` also powers the MCU's ADC supply.** The net is 125.8 mm long (92.8 mm on B.Cu,
   under the LCD) and runs from U5 to U1 pin 59. Noise on it reaches `CHx_VDIV` at 0.33x
   through R3Ux. Feed `ADC_AVDD` through its own RC filter (or from +3V3_ANA) so the
   front-end reference is not shared with the MCU.
6. **DC-sense filters are at the wrong end.** R24/C41 (and B) sit at the gain stage, then
   `CH1_DC_SENSE` runs 44 mm and `CH2_DC_SENSE` 54 mm to the MCU ADC pins, crossing XIN/XOUT,
   +1V1_CORE and the pod lines. 34 mm of `CH2_DC_SENSE` on B.Cu has no plane above it. Keep
   the 10k at the source and put the 10 nF at the MCU pin, and route over a plane.
7. **AD9288 digital-supply decoupling is distant.** VDD pins 15, 28, 33, 46 are 8.0, 6.5, 6.2
   and 4.6 mm from the nearest +3V3_DIG capacitor. Put 100 nF within about 2 mm of each.
   (Analog VD pins are fine at 1.8-4.3 mm; `ADC_VREF` C44 is fine at 2.7 mm.)
8. **240 mm of signal track is on In2.Cu**, the power-plane layer, in 13 nets (`+5V_VBUS` 76,
   `+1V1_CORE` 33, `LCD_BLK_GATE_N` 22, ...). Each cuts the +3V3 planes. Reduce it.
9. **Circuit, not routing:** the gain stage's bandwidth is set by C40x across the feedback
   resistance, so it is about 23 MHz on the high-gain range and well over 100 MHz on the
   low-gain range, and the only filter before the ADC is 33R / 22 pF (about 220 MHz). Add a
   proper anti-alias corner below Nyquist that is the same on both ranges.

### Item 9 worked through (2026-11-27, no board change made)

Poles in the signal path today (values from `hw/design.py`):

| Range | Feedback pole (Rf ‖ C40x 4.7 pF) | Output RC (R25x 33R, C42x 100 pF) | Op-amp closed loop (noise gain) | Net -3 dB |
|---|---|---|---|---|
| ±5 V (high gain, Rf 1.5k) | 22.6 MHz | 48 MHz | 3.9 | about 20 MHz |
| ±26 V (low gain, Rf 274R) | 123 MHz | 48 MHz | 1.5 | about 43 MHz |

Nyquist is 37.5 MHz at the top sample rate (75 MSPS), so the low range passes 37.5-43 MHz content
that folds back, and the two ranges show different rise times. Options, cheapest first:

1. **Done, as 51R x 100 pF (31 MHz) instead of 33R x 180 pF:** make the output RC the dominant pole on both ranges: C42/C42B 100 pF → 180 pF (0402 C0G, same
   footprint, so no placement change): 26.8 MHz. Combined with the feedback pole the high range
   drops to about 17 MHz, the low range to about 25 MHz.
2. Equalise the ranges as well: add about 22 pF across each 330R gain-switch resistor (R6A/R6B) so
   the feedback time constant stays about 7 ns when the switch closes. Needs two 0402 sites next to
   R6x, so it is a placement and routing change.
3. A second-order filter before the ADC (series L or a Sallen-Key stage) only pays off if the
   instrument is meant for spectral work; for an 8-bit pen scope option 1 is probably enough.

Slow timebases are a separate matter: with a plain slower ADC clock, 1 ms/div would sample at
48 kS/s behind a 20-43 MHz front end, and anything above 24 kHz would alias. No analog filter fixes
that, so the firmware now uses peak detect at 50 µs/div and slower: the ADC stays at 2.5 MS/s and
each output sample pair is the min and max of its slot (`scope_peak_group`, `capture_poll` in
`main.c`). Host-tested and type-checked; the main loop's throughput at 2.5 MS/s (about 15 cycles a
word) still has to be confirmed on the first board, and `g_peak_overrun` says if it fell behind.

Checked and fine: the tip / `HV_MID` section (planes removed, 0.40 mm clearance rule, 1.9 mm
`HV_MID`), `ADCx_INP` (6.8 mm, F.Cu, over AGND), the AGND-GND bridge R16 at the ADC, and the
LED feeds under the tip (DC only).
