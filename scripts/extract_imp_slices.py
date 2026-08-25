import json, sys

def extract_imp_encounter_slice(bundle_path, output_path):
    data = json.load(open(bundle_path))
    entries = [e["resource"] for e in data["entry"]]

    imp_encounters = [r for r in entries if r["resourceType"] == "Encounter" and r.get("class", {}).get("code") == "IMP"]
    if not imp_encounters:
        print("No IMP encounter found in this bundle.")
        return
    imp_enc = imp_encounters[0]
    imp_id = imp_enc["id"]
    imp_urn = f"urn:uuid:{imp_id}"

    print(f"Inpatient stay: {imp_enc['period']['start']} -> {imp_enc['period']['end']}")

    patient = next(r for r in entries if r["resourceType"] == "Patient")

    linked = []
    for r in entries:
        rt = r["resourceType"]
        if rt == "Patient":
            continue
        enc_ref = None
        if "encounter" in r:
            ref = r["encounter"]
            enc_ref = ref.get("reference") if isinstance(ref, dict) else None
        if "context" in r and isinstance(r["context"], dict):
            ctx_enc = r["context"].get("encounter")
            if ctx_enc and isinstance(ctx_enc, list):
                enc_ref = ctx_enc[0].get("reference")
        if enc_ref == imp_urn:
            linked.append(r)
        elif rt == "Encounter" and r["id"] == imp_id:
            linked.append(r)

    med_refs = set()
    for r in linked:
        if r["resourceType"] == "MedicationRequest" and "medicationReference" in r:
            med_refs.add(r["medicationReference"]["reference"])
    for r in entries:
        if r["resourceType"] == "Medication" and f"urn:uuid:{r['id']}" in med_refs:
            linked.append(r)

    resource_counts = {}
    for r in linked:
        resource_counts[r["resourceType"]] = resource_counts.get(r["resourceType"], 0) + 1
    print("Resources linked to this stay:", resource_counts)

    slice_bundle = {
        "note": "Trimmed slice — one inpatient encounter and everything linked to it",
        "patient_summary": {"id": patient["id"], "gender": patient["gender"], "birthDate": patient["birthDate"]},
        "resources": [patient] + linked,
    }
    json.dump(slice_bundle, open(output_path, "w"), indent=2)
    print(f"\nWrote focused slice to {output_path}")

if __name__ == "__main__":
    bundle_path = sys.argv[1] if len(sys.argv) > 1 else "data/samples_inpatient/example.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "data/samples_inpatient/imp_slice_example.json"
    extract_imp_encounter_slice(bundle_path, output_path)