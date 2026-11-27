#!/usr/bin/env bash
# finish.sh IN OUT : supplies, GND, AGND (nose copper hidden from the router), then a filled-board DRC.
D=$(cd "$(dirname "$0")" && pwd); WORK=${GROUTE_WORK:-/tmp/groute}; mkdir -p "$WORK"
KPY=${KICAD_PYTHON:-kicad-python}; KCLI=${KICAD_CLI:-kicad-cli}
IN=$1; OUT=$2; cd "$WORK"
J="F.Cu In1.Cu In2.Cu B.Cu"
KICAD_RIP_PREEXISTING=0 EXTRA="--power-nets /+3V3_DIG /+3V3_ANA /+1V1_CORE /+3V0_REF --power-nets-widths 0.25 0.25 0.3 0.2" "$D"/step.sh $IN.kicad_pcb ${OUT}_s.kicad_pcb "$J" "1 -1 3 1" +3V3_DIG +3V3_ANA +1V1_CORE +3V0_REF | head -1
KICAD_RIP_PREEXISTING=0 "$D"/step.sh ${OUT}_s.kicad_pcb ${OUT}_g.kicad_pcb "$J" "1 3 3 1" GND | head -1
$KPY $D/netswap.py ${OUT}_g.kicad_pcb ${OUT}_h.kicad_pcb hide 2>&1 | grep -v "Debug\|swig"
for e in kicad_pro kicad_dru; do cp ${OUT}_g.$e ${OUT}_h.$e; done
KICAD_RIP_PREEXISTING=0 "$D"/step.sh ${OUT}_h.kicad_pcb ${OUT}_a.kicad_pcb "$J" "1 3 3 1" AGND | head -1
$KPY $D/netswap.py ${OUT}_a.kicad_pcb $OUT.kicad_pcb show 2>&1 | grep -v "Debug\|swig"
for e in kicad_pro kicad_dru; do cp ${OUT}_a.$e $OUT.$e; done
for r in 1 2 3 4; do
  $KCLI pcb drc --severity-all --format json --units mm -o $OUT.n.drc.json $OUT.kicad_pcb >/dev/null 2>&1
  $KPY $D/nudge.py $OUT.kicad_pcb $OUT.kicad_pcb $OUT.n.drc.json 2>&1 | grep -v "Debug\|swig" | sed "s/^/  nudge round $r: /"
done
$KCLI pcb drc --refill-zones --schematic-parity --severity-all --format json --units mm -o $OUT.filled.drc.json $OUT.kicad_pcb >/dev/null 2>&1
python3 - $OUT.filled.drc.json <<'PY'
import json,sys,collections
d=json.load(open(sys.argv[1]))
c=collections.Counter(v['type'] for v in d['violations']); c.pop('lib_footprint_issues',None)
n=collections.Counter()
for v in d['unconnected_items']:
    for i in v['items']:
        if '[/' in i['description']: n[i['description'].split('[/')[1].split(']')[0]]+=1; break
print('FILLED DRC:',dict(c) or 'clean','| unconnected:',dict(n) or 'none')
for v in d['unconnected_items']+[x for x in d['violations'] if x['type'] not in ('lib_footprint_issues','track_dangling','via_dangling')]:
    print('   ',v['type'][:16],' ; '.join(i['description'][:52]+'@%.1f,%.1f'%(i['pos']['x']-20,i['pos']['y']-50) for i in v['items']))
PY
