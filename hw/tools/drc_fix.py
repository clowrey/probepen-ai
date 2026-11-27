"""Turn a KiCad DRC report into a touch-up spec for hw/tools/plan_routes.py.

    kicad-cli pcb drc --schematic-parity --severity-all --format json -o drc.json hw/probepen_ai.kicad_pcb
    python hw/tools/drc_fix.py drc.json spec.json && python -m hw.tools.plan_routes pads.json spec.json

Plane-net vias that sit too close to something are dropped (the pours and other vias carry the net),
dangling vias are dropped, open signal nets are re-joined and open plane pads get a new via.
"""
import json
import re
import sys

PLANE = ("GND", "AGND", "+3V3_DIG", "+3V3_ANA")
OX, OY = 20.0, 50.0


def main(drc_json, spec_json):
    d = json.load(open(drc_json))
    drop, nets, stubs = [], [], []
    for v in d["violations"]:
        items = v["items"]
        if v["type"] in ("hole_clearance", "clearance", "shorting_items", "via_dangling", "track_dangling"):
            for it in items:
                m = re.match(r"(Via|Track(?: \(arc\))?) \[/([^\]]+)\]", it["description"])
                if not m:
                    continue
                kind, net = m.group(1), m.group(2)
                x, y = it["pos"]["x"] - OX, it["pos"]["y"] - OY
                if kind == "Via" and (net in PLANE or v["type"] == "via_dangling"):
                    drop.append([round(x, 3), round(y, 3), 0.03, net])
                    break
                if kind != "Via" and v["type"] == "track_dangling":
                    drop.append([round(x, 3), round(y, 3), 0.03, net])
                if kind != "Via" and v["type"] == "clearance" and net in PLANE:
                    drop.append([round(x, 3), round(y, 3), 0.03, net])
    for v in d["unconnected_items"]:
        for it in v["items"]:
            m = re.match(r"(?:PTH pad|Pad) (\S+) \[/([^\]]+)\] of (\S+)", it["description"])
            n = re.search(r"\[/([^\]]+)\]", it["description"])
            if not n:
                continue
            net = n.group(1)
            if net in PLANE:
                if m and [net, m.group(3), m.group(1)] not in stubs:
                    stubs.append([net, m.group(3), m.group(1)])
            elif net not in [x["net"] for x in nets]:
                nets.append({"net": net, "clr": 0.105})
    json.dump({"drop": drop, "nets": nets, "stubs": stubs, "stub_clr": 0.105}, open(spec_json, "w"), indent=1)
    print(f"drop {len(drop)}, nets {[n['net'] for n in nets]}, stubs {stubs}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
