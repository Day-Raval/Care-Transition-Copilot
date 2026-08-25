import json, os
from collections import defaultdict

FHIR_DIR = "data/raw/fhir"

files = [f for f in os.listdir(FHIR_DIR) if not f.startswith(("hospitalInformation", "practitionerInformation"))]

totals = defaultdict(int)
zero_docref, zero_medreq = [], []
encounter_classes = defaultdict(int)

for fn in files:
    data = json.load(open(f"{FHIR_DIR}/{fn}"))
    counts = defaultdict(int)
    for e in data["entry"]:
        rt = e["resource"]["resourceType"]
        counts[rt] += 1
        if rt == "Encounter":
            encounter_classes[e["resource"].get("class", {}).get("code", "unknown")] += 1
    for k in ("Encounter", "Condition", "MedicationRequest", "DocumentReference"):
        totals[k] += counts[k]
    if counts["DocumentReference"] == 0:
        zero_docref.append(fn)
    if counts["MedicationRequest"] == 0:
        zero_medreq.append(fn)

print(f"{len(files)} patient bundles")
print(dict(totals))
print("Encounter classes:", dict(encounter_classes))
print("Zero DocumentReference:", len(zero_docref))
print("Zero MedicationRequest:", len(zero_medreq))
