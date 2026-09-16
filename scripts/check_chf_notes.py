"""
Sanity check: does a plain exact-text search find real CHF notes at all?
If yes, but the vector search didn't surface them, that's a retrieval
quality issue. If actual CHF notes are just sparse/generic in the
corpus, that's a data issue, not a retrieval bug.
"""
import json

count = 0
for line in open("data/processed/discharge_notes.jsonl"):
    rec = json.loads(line)
    text = (rec.get("note_text") or "").lower()
    if "heart failure" in text or "congestive" in text:
        count += 1
        if count <= 3:
            print(f"--- {rec['encounter_id'][:12]} ---")
            print(text[:300])
            print()

print(f"Total notes mentioning heart failure / congestive: {count}")