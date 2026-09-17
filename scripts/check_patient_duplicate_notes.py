# scripts/check_patient_duplicate_notes.py
import json
import sys

target_patient = sys.argv[1] if len(sys.argv) > 1 else "93a37655-bc88-fb39-8497-8f3e49a48140"

notes = []
for line in open("data/processed/discharge_notes.jsonl"):
    rec = json.loads(line)
    if rec["patient_id"] == target_patient:
        notes.append(rec)

print(f"Total encounters for this patient: {len(notes)}")
print()

seen_texts = {}
for rec in notes:
    text = rec["note_text"]
    if text in seen_texts:
        print(f"DUPLICATE TEXT found:")
        print(f"  Encounter A: {seen_texts[text]}  ({[n['discharge_ts'] for n in notes if n['encounter_id']==seen_texts[text]]})")
        print(f"  Encounter B: {rec['encounter_id']}  (discharge_ts={rec['discharge_ts']})")
    else:
        seen_texts[text] = rec["encounter_id"]

print()
print(f"Unique note texts: {len(seen_texts)} out of {len(notes)} encounters")