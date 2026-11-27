# PCB routing guide

Rules for anyone (person or agent) who places or routes this board. They are the owner's layout
preferences plus what routing this board has taught us. Where a rule is checked by a script or a
test, the file is named. Where it is not checked yet, that is said.

The aim is a board that follows good practice, not one that merely passes DRC.

## 1. What may move and what may not

- Fixed: connectors (USB-C J1, pod header J3, LCD flex pads J4, nose terminal J5), the LCD, the four
  side buttons, the mounting holes and the nose LEDs. Their positions come from the case.
- Free: every other part may be moved, rotated or put on the other side, and MCU pin assignments may
  change if that shortens or uncrosses routes. Change the pin map in `hw/design.py` (`GPIO`), then
  regenerate `firmware/include/probepen_pins.h` with `hw/tools/gen_pins_h.py`.
- Fix a routing problem at its cause first. If a net will not route, look at placement and pin
  assignment before adding vias, detours or exceptions.

## 2. Stackup and the inner layers

Four layers: F.Cu (parts and signals), In1.Cu (ground), In2.Cu (ground and supply rails), B.Cu
(signals, LCD pads, test pads).

- Both inner layers are ground. There is no dedicated power plane.
- Supplies run on In2 as wide tracks ("rails") to where they are needed. They are defined in
  `hw/rails.py` and drawn by `hw/route.py`. Use tracks, not poured strips: an autorouter keeps other
  nets' vias off a track, but will drill straight through a poured strip and cut it.
- Signals may use the inner layers as **short jumpers**, and vias through the inner layers are
  normal. What is not allowed is a long signal run on an inner layer that cuts the ground plane into
  large pieces. Working limit: 8 mm of continuous run per net per inner layer. (Not yet enforced by a
  test; check the lengths by hand until it is.)
- Prefer In2 for jumpers, so In1 stays the most complete reference under the top-side parts.
- Do not put fast nets on an inner layer: ADC data and clock, USB, the crystal. (The LCD SPI clock
  has used a few millimetres of In2 in some routings; prefer the outer layers.)
- No copper pours on any layer nose-side of the input dividers: no ground ring round the probe pads
  and no ground under them. One AGND trace serves the nose.
- Analog ground (AGND) is an island under the analog front end on every layer; digital ground (GND)
  is everywhere else.

## 3. Vias

- Always 0.40 mm pad, 0.30 mm drill: JLC's standard via. Any hole under 0.3 mm adds about $40 per order in via, Kelvin-test and Tg155 charges. 0.40 mm is JLC's minimum pad for that drill. There is no reason for a
  larger via on this board.
- No via in an SMD pad. Place it beside the pad with a short stub. (Exposed thermal pads are the
  exception.)
- Keep 0.25 mm between a via's hole and any other net's copper (`hole_clearance`).
- A via of one net must not land on or beside another net's In2 rail.
- Supply pads reach their rail through one via each, as close to the pad as the rules allow.

## 4. Track widths and clearances

Net classes are in `hw/gen_sch.py` (`NETCLASS_RULES`):

| Class | Width (mm) | Use |
|---|---|---|
| Default / LV | 0.15 | logic |
| HIGHSPEED | 0.13 | ADC data and clock |
| ANALOG | 0.18 | front-end signal nets |
| USB | 0.18 | USB data (0.15 allowed at the MCU pins) |
| POWER | 0.30 | supply branches on outer layers |

- Class clearance is 0.10 mm (the fab floor). Route wider than that wherever there is room; the
  floor is for escapes, not for long parallel runs.
- High-voltage input nets (probe tip and divider midpoints) keep 0.40 mm from everything else.
- Board edge clearance 0.30 mm.
- Use different widths on purpose: wide for supplies, narrow only where pitch demands it.

## 5. Shape of the routing

- Use 45-degree runs freely. Do not route mostly orthogonal; no 90-degree corners.
- Unused outer-layer area carries a ground pour (GND or AGND), stitched to the inner ground.
- Thermal-relief pads only where a part is hand-soldered (the two nose LEDs). Everything else
  connects solidly to pours.

## 6. Nets that need care

- **USB D+ / D-**: route as a pair, same layer, matched length, no stubs. Series resistors sit next
  to the MCU.
- **Core regulator (switch node, inductor, output and input capacitors)**: copied from a proven
  RP2350 layout (`hw/regulator_ck.py`), including its local pours, its vias and the cut-out in the
  inner layers under the switch node. Do not re-route it; route around it.
- **Crystal**: beside its MCU pins, one layer, no vias, nothing else routed under it on the top
  layer.
- **Analog front end**: short summing and high-impedance nodes; keep digital nets off the top layer
  of the analog section; divider and reference nets do not run beside clocks.
- **ADC data buses**: pin order matches the board so the two 8-bit buses reach the MCU without
  crossing each other.
- **Decoupling**: each capacitor at its pin, with its own via pair. The MCU's 3.3 V pins are fed
  from a pour on B.Cu under the chip through vias inside the pad ring; the 1.1 V core rail is a pour
  on F.Cu inside the pad ring.
- **Slow nets** (buttons, DC-sense, reset, VBUS sense, backlight gate) are the ones to move, re-pin
  or jumper on an inner layer when space is short. Never trade a fast net's quality for them.

## 7. How the board is routed

Everything is scripted; nothing is drawn by hand in the PCB editor. `hw/tools/groute/run.sh` runs the
whole flow (it needs `KRT` pointing at a KiCadRoutingTools checkout). Only open-source tools that run
locally are used; no paid or hosted routing service.

1. `hw/design.py` → `hw/gen_sch.py` → `hw/gen_pcb.py`: circuit, schematic, placement and zones.
2. `hw/tools/plan_routes.py … critical` plans the copper whose geometry matters: the front end, USB
   pair, crystal, ADC buses, supply-rail vias, the nose ground trace and the MCU fan-out (below),
   around the fixed copper of `hw/regulator_ck.py`, `hw/rails.py` and `hw/escapes.py`.
3. `hw/route.py --tracks-only` draws all of that and locks it.
4. The per-net router (KiCadRoutingTools `route.py --nets …`) routes the remaining signal nets one
   group at a time (`hw/tools/groute/order.sh`): each group gets its own layer costs and, where
   needed, a router-only keep-out (rectangles on User.2) that holds it in its lane. Several group
   orders run in parallel and the order with the fewest open nets is kept. Each step takes 10 to 60
   seconds and is graded with KiCad's DRC before the next.
5. `hw/tools/groute/finish.sh`: supplies, then GND, then AGND with the nose's AGND copper hidden from
   the router (it would otherwise re-route the nose), then `nudge.py` moves any supply or ground via
   that ended up under the 0.25 mm hole clearance.
6. `hw/tools/stitch_islands.py` gives stranded outer-layer ground pour pieces a via to their plane.
7. `hw/route.py --freeze` writes the board's copper to `hw/routes.py`; `hw/route.py` rebuilds the
   board from it.

### What made the difference (2026-11-26/27)

- **Route in groups, not all at once.** The one-button autorouter made every decision itself and
  stitched ground all over the nose. Per-group runs keep those decisions here.
- **Order matters more than effort.** Whichever group goes first gets the good lanes. Lines that fail
  in every order are not a congestion problem: test them alone. If they route alone, move them to an
  earlier group; if they fail alone, their pad is trapped and needs a placement or escape fix.
- **Fan out the MCU on purpose.** Slow MCU pins get a short top-layer stub to a via just outside the
  chip, in two staggered rows (the far row placed first), so the long runs go on the bottom layer.
  At 0.4 mm pitch with a 0.25 mm hole clearance a single row of vias cannot work.
- **Planned copper can trap pads.** Analog tracks wrapped around the gain switch (U6) and the ADC's
  mode pins (U2 pins 8, 9) left those pads no way out. `hw/escapes.py` reserves a stub and via for
  each before the analog nets are planned.
- **Pin order follows the connector.** The LCD lines leave the MCU in the same top-to-bottom order as
  the LCD pads; anything else forces crossings in the one place with no room for them.
- **Rails and inner-layer islands decide where vias can go.** A rail running along a pin row, or the
  analog island extending under digital parts, leaves pads with nowhere to drop a via. Keep rails
  away from pin rows and pads.

Router settings that matter here, each learned from a bad result:

- Set a same-net pad clearance (0.1 mm). Without it the router puts vias in pads.
- Give the router 0.30 mm hole clearance for the board's 0.25 mm rule; it settles a little under
  the value it is given. Its plane-tap routine ignores even that, hence `nudge.py`.
- Do not let it route GND/AGND where a planned trace exists: it rewrites it.
- Stop supply and ground steps from ripping finished signals (`KICAD_RIP_PREEXISTING=0`).
- Its optional 45-degree corner pass can create hole-clearance errors; re-run DRC after using it.

## 8. Done means

- KiCad DRC with schematic parity: zero errors, zero unconnected items.
- `pytest` passes.
- Every rule above holds, or the exception is written down next to the code that makes it.
- Look at the board, layer by layer, before calling it finished. A clean DRC report does not show a
  cut ground plane, a long stub or a decoupling capacitor on the wrong side of its via.
