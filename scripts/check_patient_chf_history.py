"""
Checks whether a specific patient actually has any mention of a given
condition anywhere in their notes — the necessary first step before
judging whether a retrieval result is a real miss or just "there was
nothing relevant to find." Generalized to any patient_id/search term so
it's reusable beyond this one CHF check.

Usage:
    python3 scripts/check_patient_chf_history.py 93a37655-bc88-fb39-8497-8f3e49a48140
    python3 scripts/check_patient_chf_history.py <patient_id> "some other condition"
"""

import json
import sys


def check_patient_history(patient_id: str, search_terms: list[str], notes_path: str = "data/processed/discharge_notes.jsonl"):
    found_any = False
    for line in open(notes_path):
        rec = json.loads(line)
        if rec["patient_id"] != patient_id:
            continue
        text = (rec.get("note_text") or "").lower()
        matches = [term for term in search_terms if term.lower() in text]
        if matches:
            found_any = True
            print(f"Encounter {rec['encounter_id'][:12]} ({rec['discharge_ts']}): "
                  f"contains {matches}")

    if not found_any:
        print(f"No mention of {search_terms} found anywhere in patient {patient_id[:12]}'s notes.")
        print("A retrieval 'miss' for this patient/term is NOT a bug — there was nothing to find.")
    return found_any


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_patient_chf_history.py <patient_id> [search terms...]")
        sys.exit(1)

    patient_id = sys.argv[1]
    search_terms = sys.argv[2:] if len(sys.argv) > 2 else ["heart failure", "congestive"]
    check_patient_history(patient_id, search_terms)