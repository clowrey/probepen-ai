"""open_nets.py DRC_JSON: print the signal nets a KiCad DRC report lists as unconnected (supplies and grounds left out)."""
import json
import sys

SUPPLIES = {"GND", "AGND", "+3V3_DIG", "+3V3_ANA", "+1V1_CORE", "+3V0_REF"}
d = json.load(open(sys.argv[1]))
nets = set()
for v in d["unconnected_items"]:
    for item in v["items"]:
        if "[/" in item["description"]:
            nets.add(item["description"].split("[/")[1].split("]")[0])
            break
print(" ".join(sorted(nets - SUPPLIES)))
