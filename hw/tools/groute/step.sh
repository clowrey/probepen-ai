#!/usr/bin/env bash
# One routing step: route the named nets with the per-net router, on the given layers, then grade.
#   step.sh IN OUT "F.Cu B.Cu" "1 1" NET...        (EXTRA="..." adds router flags)
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); WORK=${GROUTE_WORK:-/tmp/groute}; mkdir -p "$WORK"
KPY=${KICAD_PYTHON:-kicad-python}; KCLI=${KICAD_CLI:-kicad-cli}
IN=$1; OUT=$2; LAYERS=$3; COSTS=$4; shift 4
KRT=${KRT:?set KRT to the KiCadRoutingTools checkout}
base_in=${IN%.kicad_pcb}; base_out=${OUT%.kicad_pcb}
W=$(mktemp -d "$WORK"/w.XXXX)
cp "$IN" $W/in.kicad_pcb; cp $base_in.kicad_pro $W/in.kicad_pro; cp $base_in.kicad_dru $W/in.kicad_dru
# the router settles a little under the hole rule it is given: give it 0.30 for the board's 0.25
sed -i 's/"min_hole_clearance": 0.25/"min_hole_clearance": 0.3/' $W/in.kicad_pro
sed -i 's/hole_clearance (min 0.25mm)/hole_clearance (min 0.3mm)/' $W/in.kicad_dru
NETS=(); for n in "$@"; do NETS+=("/$n"); done
KOX=""
if [ -n "${KO:-}" ]; then   # KO="x0 y0 x1 y1 ..." : router-only keep-out rectangles for this step
  $KPY $D/ko.py $W/in.kicad_pcb $W/in.kicad_pcb add $KO 2>&1 | grep -v "Debug\|swig" | grep -i error
  KOX="--keepout --keepout-layer User.2"
fi
t0=$(date +%s)
( cd $KRT && KICAD_FINALIZE_RIP=0 .venv/bin/python py_router/route.py $W/in.kicad_pcb $W/out.kicad_pcb \
    --nets "${NETS[@]}" --layers $LAYERS --layer-costs $COSTS \
    --escalation off --no-fix-drc-settings --keep-input-copper --same-net-pad-clearance 0.1 \
    --via-size 0.4 --via-drill 0.2 --grid-step 0.05 $KOX ${EXTRA:-} ) > $base_out.log 2>&1
rc=$?
t1=$(date +%s)
[ -n "$KOX" ] && $KPY $D/ko.py $W/out.kicad_pcb $W/out.kicad_pcb clear 2>&1 | grep -v "Debug\|swig" | grep -i error
cp $W/out.kicad_pcb "$OUT" 2>/dev/null || { echo "ROUTER FAILED rc=$rc"; tail -5 $base_out.log; exit 1; }
cp $base_in.kicad_pro $base_out.kicad_pro; cp $base_in.kicad_dru $base_out.kicad_dru
rm -rf $W
$KCLI pcb drc --severity-all --format json --units mm -o $base_out.drc.json "$OUT" >/dev/null 2>&1
python3 - "$base_out.drc.json" $(( t1 - t0 )) "$@" <<'PY'
import json,sys,collections
d=json.load(open(sys.argv[1])); secs=sys.argv[2]; nets=set(sys.argv[3:])
c=collections.Counter(v['type'] for v in d['violations'])
for k in ('lib_footprint_issues','track_dangling','via_dangling'): c.pop(k,None)
openn=collections.Counter()
for v in d['unconnected_items']:
    for i in v['items']:
        if '[/' in i['description']:
            openn[i['description'].split('[/')[1].split(']')[0]]+=1; break
mine={n:openn[n] for n in nets if openn[n]}
print(f"  {secs}s | DRC {dict(c) or 'clean'} | this step still open: {mine or 'none'} | open nets on board: {len(openn)}")
for v in d['violations']:
    if v['type'] in ('clearance','shorting_items','hole_clearance','tracks_crossing') and any(n in v['items'][0]['description']+v['items'][-1]['description'] for n in nets):
        print('    ',v['type'],' ; '.join(i['description'][:48]+'@%.1f,%.1f'%(i['pos']['x']-20,i['pos']['y']-50) for i in v['items']))
PY
