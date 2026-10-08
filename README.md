# Care Transition Copilot

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![JavaScript / JSX](https://img.shields.io/badge/javascript%20%2F%20jsx-frontend-f7df1e.svg)](web/)
[![CSS](https://img.shields.io/badge/css-styles-663399.svg)](web/src/styles.css)
[![HTML](https://img.shields.io/badge/html-vite%20entry-e34f26.svg)](web/index.html)
[![Shell](https://img.shields.io/badge/shell-scripts-4eaa25.svg)](scripts/)
[![PowerShell](https://img.shields.io/badge/powershell-windows%20scripts-5391fe.svg)](scripts/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Care Transition Copilot is a synthetic-data healthcare AI demo that scores
30-day readmission risk, retrieves patient-specific chart context, drafts a
care-transition plan, and keeps a clinician in control before anything is
approved.

> Research and demo software only. Not for clinical use, not HIPAA validated,
> and not yet approved for real patient data or protected health information.

## Preview

![High-risk discharge workflow](Docs/readme_workflow.svg)

![Clinician review preview](Docs/readme_clinician_review.svg)

## What It Does

- Scores recent discharges with a Cox survival model served by FastAPI.
- Retrieves section-aware discharge-note context from a patient-scoped ChromaDB
  vector store.
- Uses a LangGraph agent flow to draft and critique care-transition plans.
- Requires clinician approve/reject decisions before report generation or
  handoff records.
- Supports local API-key demo mode and OIDC/RBAC mode for deployed user access.
- Records audit events, decisions, care plans, notifications, FHIR write-back
  stubs, follow-ups, reminders, metrics, and optional Kafka events.

## MVP Architecture and Production Enhancements

The MVP is a local, clinician-in-the-loop demo: a React app calls a FastAPI
service, FastAPI runs the readmission model and LangGraph care-plan workflow,
and the workflow retrieves patient-scoped synthetic chart context before
drafting and critiquing a plan. Optional local services let the same code path
exercise Postgres, Redis, Kafka, notifications, and observability without
claiming a production deployment.

```mermaid
flowchart LR
    clinician["Clinician / Care Coordinator"] --> web["React SPA<br/>queue, patient detail, chat, approvals"]
    web --> auth["API key demo mode<br/>or OIDC/RBAC mode"]
    auth --> api["FastAPI Service<br/>risk, chat, reports, decisions"]

    api --> model["Local risk model artifact<br/>30-day readmission score"]
    api --> workflow["LangGraph Workflow<br/>risk gate, retrieval, draft, critique"]
    workflow --> vector["Local ChromaDB<br/>patient-scoped note chunks"]
    workflow --> llm["Groq-hosted LLM<br/>reasoning, critique, chat tools"]

    data["Synthetic FHIR / HL7 samples"] --> ingestion["Ingestion and feature scripts"]
    ingestion --> model
    ingestion --> vector

    api -. optional .-> db["Postgres<br/>decisions, plans, audit"]
    api -. optional .-> cache["Redis<br/>assessment cache"]
    api -. optional .-> kafka["Kafka<br/>discharge and audit events"]
    api -. optional .-> metrics["Prometheus / Grafana<br/>health, latency, drift"]
    api -. stub .-> notify["Notification provider"]
    api -. stub .-> ehrWrite["FHIR write-back"]
```

| Area | MVP today | Production enhancement |
| --- | --- | --- |
| Identity and access | API-key demo mode plus OIDC/RBAC support | Enforce enterprise IdP, signed access tokens, route-level RBAC, session policy, and no browser service secrets |
| API edge | Local FastAPI service with request IDs and safe errors | Add HTTPS, API gateway, WAF/rate limits, restricted CORS, managed secrets, and environment-specific config |
| Clinical workflow | Risk-gated drafting, critique, and clinician approve/reject controls | Add site-specific policy checks, escalation rules, human review queues, and approved handoff workflows |
| Data sources | Synthetic Synthea FHIR/HL7 records and generated notes | Connect to approved EHR feeds, validate schemas, de-identify or protect PHI, and define retention/deletion workflows |
| Persistence | Local files by default; optional Postgres and Redis | Use managed encrypted storage, backups, migrations, audit retention, and cache invalidation controls |
| Event processing | Optional Kafka topics and idempotent consumer paths | Add production brokers, retries, dead-letter queues, backpressure controls, replay procedures, and runbooks |
| Model operations | Versioned local artifact, prediction logs, drift report, fairness audit | Calibrate on representative data, validate clinically, monitor drift/fairness, and require model approval before use |
| Observability | Health checks, Prometheus metrics, and Grafana dashboards locally | Add hosted alerting, SLOs, redacted traces, incident response, and dependency outage monitoring |
| External integrations | Notification and FHIR write-back stubs | Integrate approved notification, EHR write-back, and LLM providers through security/privacy review |

Current stubs intentionally keep real EHR write-back, automated reminder
dispatch, managed secrets, hosted alerting, and production deployment outside
the MVP boundary. See [ARCHITECTURE.md](ARCHITECTURE.md) and
[SECURITY.md](SECURITY.md) for the deeper design.

## Current Status

| Area | Status |
| --- | --- |
| Synthetic FHIR/HL7 ingestion | Implemented |
| 30-day readmission target and baseline model | Implemented; diagnostic only |
| Fairness audit | Implemented; currently inconclusive at this dataset size |
| Retrieval and vector store | Implemented locally |
| Agent drafting and critique | Implemented with Groq-hosted models |
| Clinician React UI | Implemented locally |
| API-key and OIDC/RBAC auth | Implemented |
| Postgres, Redis, Kafka, Twilio, Prometheus/Grafana | Optional local integrations |
| Real EHR write-back and automated reminders | Stubbed/planned |
| Managed production deployment | Planned |

## Quick Start

### Prerequisites

- Python 3.12+
- `uv` or `pip`
- Node.js 20+
- Java 11+ if regenerating Synthea data
- Docker + Docker Compose for optional Postgres, Redis, Kafka, or observability
- `GROQ_API_KEY`, `API_KEY`, and matching `VITE_API_KEY` in `.env`

Copy the environment template first:

```bash
cp .env.example .env
```

### Install Python Dependencies

Use either dependency path. The repo keeps both because `requirements.txt` is
the operational install path, while `pyproject.toml` supports uv-native project
workflows.

```bash
# Option A: requirements.txt with uv
uv venv
uv pip install -r requirements.txt
uv pip install -e .
```

```bash
# Option B: pyproject.toml with uv
uv sync --dev
```

```bash
# Option C: pip
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

On PowerShell, activate with:

```powershell
.venv\Scripts\Activate.ps1
```

### Build Data and Model Artifacts

If your checkout already has local generated artifacts, you can skip to running
the app. For a clean clone, rebuild them:

```bash
./scripts/generate_synthetic_data.sh
python scripts/export_records.py
python -m src.features.target
python -m src.model.run_experiment --alpha 1.0 --notes "baseline, 6 features"
python -m src.embeddings.build_vector_store
python -m src.embeddings.validate_vector_store
```

### Run the API and Web App

From WSL or macOS/Linux:

```bash
bash dev.sh
```

From PowerShell:

```powershell
cd web
npm run dev:full:win
```

Open `http://127.0.0.1:5173/`.

## API Smoke Test

Start FastAPI if you are not using `dev.sh`:

```bash
uvicorn src.api.main:app --reload --port 8080
```

Then check the service:

```bash
curl http://127.0.0.1:8080/health
curl -H "X-API-Key: $API_KEY" http://127.0.0.1:8080/model-info
```

Example prediction:

```bash
curl -X POST http://127.0.0.1:8080/predict \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "age_at_discharge": 72,
    "length_of_stay_days": 5,
    "medication_count": 9,
    "prior_admissions_90d": 1,
    "med_flag_diuretic": true,
    "med_flag_anticoagulant": false
  }'
```

## Common Commands

```bash
# Run all automated checks used by CI
bash scripts/check.sh

# Windows equivalent
powershell -ExecutionPolicy Bypass -File scripts/check.ps1

# Run API only
bash dev.sh --api-only

# Run selected model diagnostics
python -m src.model.compare_models
python -m src.model.fairness_audit

# Run the agent pipeline for one patient
python -m src.agents.orchestrator <patient_id>

# Start local observability
docker compose -f docker-compose.observability.yml up
```

## Project Structure

```text
src/
|-- agents/         # Risk tool, retrieval, reasoning, critique, chat, orchestration
|-- api/            # FastAPI app, auth, persistence, metrics, drift, reports
|-- data_services/  # Kafka, notifications, idempotency, runtime data services
|-- embeddings/     # Note chunking and ChromaDB build/validation
|-- features/       # Readmission target construction
|-- ingestion/      # FHIR/HL7 parsing, temporal filters, episode clustering
|-- model/          # Training, comparison, experiment registry, fairness audit
`-- retrieval/      # Patient-scoped vector queries

web/
|-- src/            # React clinician UI
`-- package.json    # Vite scripts

database/           # Postgres migration and verification helpers
Docs/               # Architecture, detailed README, data docs, diagrams
tests/              # Unit and integration-style regression tests
```

## Documentation

- [Detailed implementation notes](Docs/README_DETAILED.md)
- [Architecture](ARCHITECTURE.md)
- [Security model](SECURITY.md)
- [Data card](Docs/DATA_CARD.md)
- [Model card](Docs/MODEL_CARD.md)
- [Changelog](CHANGELOG.md)
- [FHIR/HL7 data documentation](Docs/data_documentation.md)
- [Database migration notes](database/README.md)

## Data and Model Limits

This repo uses synthetic Synthea data only. The current modeling workflow is
diagnostic: the available readmission event count is small, the risk score is a
relative model output rather than a calibrated probability, and subgroup
fairness results are not yet statistically determinate.

Before any real clinical or PHI use, the project would need clinical validation,
calibration, privacy review, security review, deployment hardening, monitoring,
retention/deletion workflows, and approved integrations with the target EHR and
model providers.

## Contributing

Issues and pull requests should use synthetic data only. See
[CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
and [SECURITY.md](SECURITY.md) before opening changes that touch auth, patient
data flow, model behavior, or generated clinical text.

## License

MIT. See [LICENSE](LICENSE).
