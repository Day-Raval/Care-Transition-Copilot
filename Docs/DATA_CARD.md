# Data Card

## Dataset Summary

Care Transition Copilot uses synthetic patient data generated with Synthea and
derived local artifacts for modeling, retrieval, and workflow demos. The project
does not require real patient data or a data use agreement for local demos.

## Data Sources

- Synthetic FHIR bundles under `data/`.
- Parsed discharge episodes in CSV form.
- Synthetic discharge notes in JSONL form.
- Derived labels for 30-day readmission experiments.
- Local runtime artifacts such as care plans, decisions, audit events, and
  notification records.

## Sensitive Data Policy

Use synthetic data only. Do not commit or process real PHI, credentials, access
tokens, private logs, or patient-identifying screenshots in this repository.

## Processing

The ingestion pipeline parses FHIR/HL7-style records, clusters encounters into
hospitalization episodes, filters conditions and medications to discharge-time
knowledge, exports structured features, and keeps note text separate from
tabular model inputs for retrieval.

## Known Limitations

- Synthetic data does not prove performance on real-world clinical data.
- Some synthetic subgroups have too few events for reliable fairness estimates.
- Local artifacts are demo storage, not durable compliance-grade audit storage.
- Any move to real data requires privacy, security, legal, clinical, and vendor
  reviews.

## Rebuild Commands

```bash
./scripts/generate_synthetic_data.sh
python scripts/export_records.py
python -m src.features.target
python -m src.embeddings.build_vector_store
```
