# scripts/export_records.py
import sys, pandas as pd
sys.path.insert(0, ".")
from src.ingestion.fhir_parser import parse_all_bundles

records, failures = parse_all_bundles("data/raw/fhir")
df = pd.DataFrame([r.__dict__ for r in records])
df.to_csv("data/processed/discharge_records.csv", index=False)
print(f"{len(df)} rows written, {len(failures)} failures")