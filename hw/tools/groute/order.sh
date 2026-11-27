#!/usr/bin/env bash
# order.sh P START ORDER : route the signal groups in ORDER (letters) from START.kicad_pcb into P1..Pn, then leftovers.
D=$(cd "$(dirname "$0")" && pwd); WORK=${GROUTE_WORK:-/tmp/groute}; mkdir -p "$WORK"
KPY=${KICAD_PYTHON:-kicad-python}; KCLI=${KICAD_CLI:-kicad-cli}
P=$1; S=$2; ORD=$3
cd "$WORK"
for e in kicad_pcb kicad_pro kicad_dru; do cp $S.$e ${P}0.$e; done
O="F.Cu In1.Cu In2.Cu B.Cu"; OC="1 -1 4 1"; J="$O"; JC="1 -1 10 1"
k=0; prev=${P}0
for g in $(echo $ORD | grep -o .); do
  k=$((k+1)); out=${P}$k
  case $g in
    S) KO="62 -9 102 0.5 84 0.5 102 2.5" "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" CH1_DC_SENSE CH2_DC_SENSE BTN_DOWN_N BTN_UP_N BTN_MODE_N VBUS_SENSE RUN_N ;;
    B) KO="62 -9 102 0.5 84 0.5 102 2.5" "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" BTN_UP_N RUN_N ;;
    K) "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" BTN_ASK_AI_N ;;
    A) "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" ADC_S1 ADC_S2 ;;
    G) KO="62 0.5 92 9" "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" CH1_GAIN_LO CH2_GAIN_LO ADC_S1 ADC_S2 ;;
    D) "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" QSPI_SS_N BOOT_KEY LCD_BLK_GATE_N LCD_BLK_SW LCD_LEDA BTN_ASK_AI_N ;;
    L) "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" LCD_SCK LCD_MOSI LCD_CS_N LCD_DC LCD_RST_N ;;
    P) "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" POD_CH0 POD_CH1 POD_CH2 POD_CH3 POD_CH0_RAW POD_CH1_RAW POD_CH2_RAW POD_CH3_RAW +3V3_POD ;;
    U) "$D"/step.sh $prev.kicad_pcb $out.kicad_pcb "$O" "$OC" USB_CC1 USB_CC2 VBUS_RAW +5V_ANA_IN ;;
  esac > /dev/null 2>&1
  prev=$out
done
LEFT=$(python3 - $prev.drc.json <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); n=set()
for v in d['unconnected_items']:
    for i in v['items']:
        if '[/' in i['description']: n.add(i['description'].split('[/')[1].split(']')[0]); break
print(' '.join(sorted(x for x in n if x not in ('GND','AGND','+3V3_DIG','+3V3_ANA','+1V1_CORE','+3V0_REF'))))
PY
)
fin=${P}L
if [ -n "$LEFT" ]; then
  EXTRA="--force-reroute --rip-existing-nets /BTN_* /POD_* /BOOT_KEY /QSPI_SS_N /RUN_N /LCD_BLK_* /LCD_LEDA" "$D"/step.sh $prev.kicad_pcb $fin.kicad_pcb "$J" "$JC" $LEFT > /dev/null 2>&1
else for e in kicad_pcb kicad_pro kicad_dru drc.json; do cp $prev.$e $fin.$e; done; fi
python3 - $fin.drc.json "$ORD" "$P" <<'PY'
import json,sys,collections
d=json.load(open(sys.argv[1])); n=collections.Counter()
for v in d['unconnected_items']:
    for i in v['items']:
        if '[/' in i['description']: n[i['description'].split('[/')[1].split(']')[0]]+=1; break
c=collections.Counter(v['type'] for v in d['violations']); [c.pop(k,None) for k in ('lib_footprint_issues','track_dangling','via_dangling')]
sig={k:v for k,v in n.items() if k not in ('GND','AGND','+3V3_DIG','+3V3_ANA','+1V1_CORE','+3V0_REF')}
print(f"RESULT {sys.argv[3]} order {sys.argv[2]}: {len(sig)} signal nets open {sorted(sig)} | DRC {dict(c) or 'clean'}")
PY
