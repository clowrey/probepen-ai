#!/usr/bin/env bash
# Re-route the whole board after a circuit or placement change. See hw/routes.py for the steps.
# Needs: KiCad Python (kicad-python), the project venv, and KiCadRoutingTools
# (https://github.com/drandyhaas/KiCadRoutingTools, KRT=/path/to/KiCadRoutingTools with its own .venv).
set -euo pipefail
cd "$(dirname "$0")/../.."
KPY=${KICAD_PYTHON:-kicad-python}
PY=${PYTHON:-.venv/bin/python}
KRT=${KRT:?set KRT to the KiCadRoutingTools checkout}
TMP=${TMPDIR:-/tmp}/probepen_reroute
rm -rf "$TMP"; mkdir -p "$TMP"
"$PY" -m hw.gen_sch
"$KPY" hw/gen_pcb.py
"$KPY" hw/tools/dump_pads.py hw/probepen_ai.kicad_pcb "$TMP/pads.json"
"$PY" -m hw.tools.plan_routes "$TMP/pads.json" critical          # nets whose geometry matters
"$KPY" hw/route.py --tracks-only                                 # ... drawn and locked
cp hw/probepen_ai.kicad_pcb "$TMP/in.kicad_pcb"; cp hw/probepen_ai.kicad_pro "$TMP/in.kicad_pro"; cp hw/probepen_ai.kicad_dru "$TMP/in.kicad_dru"
( cd "$KRT" && .venv/bin/python -c "import sys; sys.path[:0]=['py_router','.']; from protected_nets import persist_same_net_pad_clearance as p; p('$TMP/in.kicad_pro', 0.1, verbose=False)" \
  && .venv/bin/python py_router/autoroute.py "$TMP/in.kicad_pcb" "$TMP/out.kicad_pcb" --finish --keep-placement --board-rules --plane-layer-cost -1 )
cp "$TMP/out.kicad_pcb" hw/probepen_ai.kicad_pcb
"$KPY" hw/route.py --finish --freeze                             # plane vias, fill, freeze into hw/routes.py
"$KPY" hw/gen_pcb.py
"$KPY" hw/route.py                                               # rebuild from the frozen list
