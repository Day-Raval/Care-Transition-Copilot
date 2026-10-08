# Changelog

All notable project changes should be summarized here. Keep detailed design and
run notes in `Docs/` or `results/`.

## Unreleased

- Reworked the top-level README into a concise GitHub landing page.
- Moved the previous long README to `Docs/README_DETAILED.md`.
- Added project hygiene files: license, contribution guide, code of conduct,
  CI workflow, model card, data card, and local check scripts.
- Kept both dependency workflows supported: `requirements.txt` for operational
  installs and `pyproject.toml` for uv-native project installs.

## Local MVP Baseline

- Implemented synthetic FHIR/HL7 ingestion, discharge episode construction,
  feature export, 30-day readmission target labeling, and baseline survival
  modeling.
- Added patient-scoped retrieval, section-aware note chunking, ChromaDB vector
  store build/validation, risk-gated agent orchestration, reasoning and critique
  agents, and tool-calling chat.
- Added FastAPI serving, API-key/OIDC auth, RBAC checks, audit logging,
  persistence options, Kafka integration, notifications, follow-up/reminder
  records, metrics, and a React clinician workflow.
