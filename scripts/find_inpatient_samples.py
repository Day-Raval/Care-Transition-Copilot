import json, os, shutil

FHIR_DIR = "data/raw/fhir"
OUT_DIR = "data/samples_inpatient"

def main():
    files = [f for f in os.listdir(FHIR_DIR) if not f.startswith(("hospitalInformation", "practitionerInformation"))]

    candidates = []  # (filename, imp_count, has_medreq, has_condition, size_bytes)

    for fn in files:
        path = os.path.join(FHIR_DIR, fn)
        data = json.load(open(path))
        imp_count = 0
        has_medreq = False
        has_condition = False
        for e in data["entry"]:
            r = e["resource"]
            rt = r["resourceType"]
            if rt == "Encounter" and r.get("class", {}).get("code") == "IMP":
                imp_count += 1
            if rt == "MedicationRequest":
                has_medreq = True
            if rt == "Condition":
                has_condition = True
        if imp_count > 0:
            size = os.path.getsize(path)
            candidates.append((fn, imp_count, has_medreq, has_condition, size))

    print(f"Scanned {len(files)} bundles.")
    print(f"Found {len(candidates)} patients with at least one IMP encounter.\n")

    if not candidates:
        print("No inpatient encounters found — check FHIR_DIR path and re-run generation.")
        return

    rich = [c for c in candidates if c[2] and c[3]]
    multi_imp = sorted([c for c in candidates if c[1] >= 2], key=lambda x: -x[1])
    single_imp_rich = sorted([c for c in rich if c[1] == 1], key=lambda x: x[4])

    picks = []
    picks.extend(multi_imp[:2])
    for c in single_imp_rich:
        if c not in picks:
            picks.append(c)
        if len(picks) >= 5:
            break

    print("Selected for manual review:")
    print(f"{'Filename':<55} {'IMP#':>5} {'MedReq':>7} {'Cond':>5} {'Size':>10}")
    os.makedirs(OUT_DIR, exist_ok=True)
    for fn, imp_count, has_med, has_cond, size in picks[:5]:
        print(f"{fn:<55} {imp_count:>5} {str(has_med):>7} {str(has_cond):>5} {size:>10,}")
        shutil.copy(os.path.join(FHIR_DIR, fn), os.path.join(OUT_DIR, fn))

    print(f"\nCopied {min(len(picks),5)} files to {OUT_DIR}/")

if __name__ == "__main__":
    main()