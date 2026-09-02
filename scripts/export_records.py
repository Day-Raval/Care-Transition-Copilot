"""
Exports parsed records into two separate files, matching how the two
downstream consumers actually use this data:

1. discharge_records.csv — fully tabular, one row per inpatient encounter,
   every column a scalar (number/category/flag). This is what the risk
   model trains on. No free text, no embedded newlines.

2. discharge_notes.jsonl — one JSON object per line, keyed by encounter_id,
   holding the raw note text. This is what gets chunked and embedded into
   ChromaDB for the retrieval agent. Free text belongs here, not in the
   tabular file — a CSV column with embedded newlines is technically valid
   (RFC 4180 quoting) but unreadable in a plain text editor and awkward for
   every tabular tool downstream.
"""

import json
import sys

import pandas as pd

sys.path.insert(0, ".")
from src.ingestion.fhir_parser import parse_all_bundles
from src.utils.config import load_config

cfg = load_config()
records, failures = parse_all_bundles(cfg.fhir_dir, cfg)

# --- Structured, tabular file for the risk model ---
tabular_rows = []
for r in records:
    row = r.__dict__.copy()
    row.pop("discharge_note_text")  # excluded from the tabular file entirely
    row["comorbidity_categories"] = ",".join(row["comorbidity_categories"])  # flatten list -> string
    for flag, val in row.pop("high_risk_med_flags").items():
        row[f"med_flag_{flag}"] = val
    for attr, val in row.pop("protected_attributes").items():
        row[f"protected_{attr}"] = val
    tabular_rows.append(row)

df = pd.DataFrame(tabular_rows)
df.to_csv(cfg.output_csv, index=False)

# --- Notes file for the retrieval agent / vector store ---
with open(cfg.output_notes, "w") as f:
    for r in records:
        f.write(json.dumps({
            "encounter_id": r.encounter_id,
            "patient_id": r.patient_id,
            "discharge_ts": r.discharge_ts,
            "note_text": r.discharge_note_text,
        }) + "\n")

print(f"{len(df)} rows written to {cfg.output_csv} (structured, no free text)")
print(f"{len(records)} notes written to {cfg.output_notes} (for the vector store)")
print(f"Failures: {len(failures)}")