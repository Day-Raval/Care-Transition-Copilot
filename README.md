# Care Transition Copilot

An AI system that predicts 30-day hospital readmission risk, retrieves the
relevant patient context, and drafts a personalized follow-up plan — with a
clinician reviewing and approving every plan before it reaches a patient
record.

## What this is

Three layers, each doing the job it's suited for:

- **ML** — a survival/hazard model scores readmission risk and is audited
  for fairness across patient subgroups.
- **Agents** — a single retrieval agent pulls chart context; a coordinated
  team of agents drafts a follow-up plan and runs it past a second model
  as a checklist-based critique.
- **Human review** — nothing reaches the patient or their record without
  explicit clinician sign-off in the web app.

See `docs/proposal.docx` for the full write-up, `docs/architecture_v3.png`
for the system architecture, and `docs/data_documentation.md` for the data
schemas this project runs on.

## Status

🚧 MVP in progress — see the 4-week build plan in the proposal doc.

## Getting started

### Prerequisites

- Python 3.11+
- Docker + Docker Compose
- Java 11+ (for Synthea, the synthetic data generator)
- `uv` (or `poetry`) for dependency management

### Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd care-transition-copilot

# 2. Copy env template and fill in real values
cp .env.example .env

# 3. Start the data layer (Postgres, ChromaDB, Redis)
docker compose up -d

# 4. Install Python dependencies
uv sync   # or: poetry install

# 5. Generate synthetic patient data
./scripts/generate_synthetic_data.sh

# 6. Run the ingestion pipeline on the generated data
python -m src.ingestion.fhir_parser
```

### Running the API

```bash
uvicorn src.api.main:app --reload --port 8080
```

### Running tests

```bash
pytest tests/
```

## Project structure

src/
├── ingestion/ # HL7v2 / FHIR parsing → canonical DischargeRecord
├── features/ # Feature engineering (comorbidity grouping, med flags, etc.)
├── model/ # Risk model training + SHAP + Fairlearn audit
├── agents/ # LangGraph orchestrator + retrieval/reasoning/critique agents
├── api/ # FastAPI service (Model Serving API)
└── frontend/ # Clinician-facing web app


## Data

This project uses **synthetic data only** ([Synthea](https://synthetichealth.github.io/synthea/)) —
no real patient data or data use agreement is required to run or demo it.
See `docs/data_documentation.md` for the full HL7v2/FHIR schema reference.

## Tech stack

| Layer | Tools |
|-------|-------|
| Risk model | scikit-survival, SHAP, Fairlearn |
| API        | FastAPI |
| Agents     | LangGraph, LlamaIndex, Groq API |
| Data       | Postgres, ChromaDB, Redis |
| Frontend   | React (or Streamlit for MVP) |