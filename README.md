# ProbePen-AI (RP2354B + 50MSPS Mixed-Signal AI Copilot Probe)

USB-C pen-shaped mixed-signal oscilloscope (50MSPS), precision DC multimeter, adjustable-threshold logic probe, and 4-channel protocol sniffer designed for live MCP / AI circuit debugging.

## Repository Structure
- `hardware/probepen_ai.kicad_pcb` — Pre-generated KiCad 8/9 4-layer PCB mechanical layout (`15.0 x 132.0 mm`), `Edge.Cuts`, Layer-2 AFE ground cutout zone, mounting holes, and silkscreen component placement guides.
- `hardware/SCHEMATIC_WIRING_SPEC.md` — Complete verified circuit equations & pin-by-pin KiCad schematic wiring guide.
- `hardware/bom/jlcpcb_bom.csv` — Complete BOM with LCSC `Cxxxxxx` part numbers optimized for <$30/unit @ Qty 10 on JLCPCB.
- `hardware/pcb_datum.py` — Single source of truth for PCB & component coordinates (shared with `build123d`).
- `firmware/ms9280_capture.pio` — RP2354B PIO0 50MSPS zero-CPU parallel ADC capture program.
- `cad/enclosure_build123d.py` — Parametric `build123d` enclosure scaffold importing `pcb_datum.py`.

## Status and provenance
Generated from a Google AI Studio design chat ("Designing a Handheld Logic Probe", 2026-11-24). Nothing here is checked against datasheets yet:
- **LCSC part numbers are unverified.** Look up each one on lcsc.com before ordering. Some may not exist or may point to the wrong part.
- The reference string math assumes R9 = 2.50k (3.000 V × 2.5/30 = 0.250 V). The BOM lists 2.49k, which gives 0.249 V.
- `hardware/probepen_ai.kicad_pcb` contains only the outline, stackup, AFE keepout zone and placement guides. It has no schematic and no footprints yet. KiCad 10 loads it, and DRC passes with 0 violations.

## Workflow to Order from JLCPCB
1. Open KiCad 8/9 -> Create Project `probepen_ai` inside `hardware/` (keep the generated `probepen_ai.kicad_pcb`).
2. Enter the 5 schematic sheets using `hardware/SCHEMATIC_WIRING_SPEC.md` and assign LCSC numbers from `hardware/bom/jlcpcb_bom.csv`.
3. Press **F8** in KiCad PCB Editor (*Update PCB from Schematic*), snap footprints onto the pre-drawn silkscreen target boxes, route traces (4-layer `JLC04161H-7628`: `Sig / GND / PWR / Sig`), and run **DRC**.
4. Export Gerbers + Drill + `POS` placement file (use the `JLCPCB KiCad Fabrication Toolkit` plugin for 1-click export).
