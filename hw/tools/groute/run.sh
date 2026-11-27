#!/usr/bin/env bash
# Route the whole board group by group (docs/ROUTING_GUIDE.md, section 7) and freeze the result into hw/routes.py.
#   KRT=~/tools/KiCadRoutingTools KICAD_PYTHON=... KICAD_CLI=... hw/tools/groute/run.sh
# 1. regenerate schematic and board, plan + lock the critical copper (hw/tools/plan_routes.py critical)
# 2. route the signal groups in several orders in parallel (order.sh), keep the order with fewest open nets
# 3. supplies and grounds (finish.sh), pour-piece stitching (hw/tools/stitch_islands.py)
# 4. copy the result into hw/probepen_ai.kicad_pcb and freeze it into hw/routes.py
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$D/../../.." && pwd)
WORK=${GROUTE_WORK:-/tmp/groute}; export GROUTE_WORK=$WORK; mkdir -p "$WORK"
KPY=${KICAD_PYTHON:-kicad-python}; PY=${PYTHON:-$ROOT/.venv/bin/python}
ORDERS=${ORDERS:-"BKPALGSDU BKAPLGSDU PBKALGSDU BKALPGSDU KBPALGSDU BPKALGSDU BKAPGLSDU PKBALGSDU"}
cd "$ROOT"
"$PY" -m hw.gen_sch
"$KPY" hw/gen_pcb.py
"$KPY" hw/tools/dump_pads.py hw/probepen_ai.kicad_pcb "$WORK/pads.json"
"$PY" -m hw.tools.plan_routes "$WORK/pads.json" critical
"$KPY" hw/route.py --tracks-only
for e in kicad_pcb kicad_pro kicad_dru; do cp hw/probepen_ai.$e "$WORK/start.$e"; done
for o in $ORDERS; do "$D/order.sh" o$o start $o & done; wait
best=$(for o in $ORDERS; do "$PY" - "$WORK/o${o}L.drc.json" "$o" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); n = set()
for v in d["unconnected_items"]:
    for i in v["items"]:
        if "[/" in i["description"]:
            n.add(i["description"].split("[/")[1].split("]")[0]); break
sig = [x for x in n if x not in ("GND", "AGND", "+3V3_DIG", "+3V3_ANA", "+1V1_CORE", "+3V0_REF")]
bad = sum(1 for v in d["violations"] if v["type"] not in ("lib_footprint_issues", "track_dangling", "via_dangling"))
print(len(sig) * 100 + bad, sys.argv[2])
PY
done | sort -n | head -1 | awk '{print $2}')
echo "best order: $best"
"$D/finish.sh" "o${best}L" final
# leftover signal nets: one more pass that may hop on either inner layer and move other slow lines
REPORT="$WORK/final.filled.drc.json"
for COSTS in "1 10 10 1" "1 -1 6 1" "1 4 4 1"; do
  LEFT=$("$PY" "$D/open_nets.py" "$REPORT")
  [ -z "$LEFT" ] && break
  echo "leftovers ($COSTS): $LEFT"
  KICAD_RIP_PREEXISTING=1 EXTRA="--force-reroute --max-ripup 5 --rip-existing-nets /BTN_* /POD_* /BOOT_KEY /QSPI_SS_N /LCD_BLK_* /RUN_N /ADC_S1 /ADC_S2 /CH1_GAIN_LO /CH2_GAIN_LO" \
    "$D/step.sh" "$WORK/final.kicad_pcb" "$WORK/final2.kicad_pcb" "F.Cu In1.Cu In2.Cu B.Cu" "$COSTS" $LEFT
  for e in kicad_pcb kicad_pro kicad_dru drc.json; do cp "$WORK/final2.$e" "$WORK/final.$e"; done
  REPORT="$WORK/final.drc.json"
done
"$KPY" hw/tools/stitch_islands.py "$WORK/final.kicad_pcb" "$WORK/final.kicad_pcb"
cp "$WORK/final.kicad_pcb" hw/probepen_ai.kicad_pcb
# unused fan-out / escape vias and the stubs they leave: removed (signal nets only)
for r in 1 2 3; do
  "${KICAD_CLI:-kicad-cli}" pcb drc --severity-all --format json --units mm -o "$WORK/prune.json" hw/probepen_ai.kicad_pcb >/dev/null 2>&1
  "$KPY" hw/tools/prune_dangling.py hw/probepen_ai.kicad_pcb "$WORK/prune.json"
done
"$KPY" hw/route.py --freeze
"$KPY" hw/gen_pcb.py
"$KPY" hw/route.py
