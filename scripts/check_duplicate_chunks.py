# scripts/check_duplicate_chunks.py
import json
from collections import Counter

encounter_ids = []
for line in open("data/processed/discharge_notes.jsonl"):
    rec = json.loads(line)
    encounter_ids.append(rec["encounter_id"])

counts = Counter(encounter_ids)
dupes = {k: v for k, v in counts.items() if v > 1}
print(f"Total notes: {len(encounter_ids)}")
print(f"Duplicate encounter_ids in source file: {len(dupes)}")
for eid, count in list(dupes.items())[:5]:
    print(f"  {eid}: appears {count} times")