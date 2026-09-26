# Software Requirements Specification

# Care Transition Copilot

## 1. Introduction

This Software Requirements Specification defines the functional and
non-functional requirements for the Care Transition Copilot repository. The
system is a local MVP that predicts relative 30-day readmission risk, retrieves
patient-specific chart context, drafts and critiques care plans, and routes
drafts through clinician review.

The requirements below are written to match the implemented repository while
also identifying planned production work.

## 2. System Overview

Care Transition Copilot contains these major subsystems:

- Data ingestion and transformation from synthetic FHIR bundles.
- Feature engineering and 30-day readmission target labeling.
- Survival-model training, comparison, registry, and fairness audit.
- FastAPI model and workflow API.
- ChromaDB-backed retrieval over section-aware discharge-note chunks.
- LangGraph orchestration for risk assessment, retrieval, reasoning, and
  critique.
- React clinician web application.
- Local audit, care-plan, decision, and report persistence.

## 3. External Interfaces

### 3.1 User Interfaces

The React app shall provide:

- Risk queue view at `/`.
- Ask a question view at `/chat`.
- Patients view at `/patients`.
- Care plans view at `/care-plans`.

The UI shall communicate with the FastAPI backend using configured API base URL,
API key, request timeout, and assessment timeout settings.

### 3.2 API Interfaces

The FastAPI service shall expose:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Return model and dependency readiness. |
| GET | `/model-info` | Return configured model metadata and fairness caveat. |
| POST | `/predict` | Return risk score, percentile, category, and disclaimer. |
| GET | `/drift-report` | Return feature and prediction drift report. |
| GET | `/patients` | Return risk queue items. |
| GET | `/patients/{patient_id}/assessment` | Generate or return cached full assessment. |
| GET | `/patients/{patient_id}/decision` | Return latest decision for a patient episode. |
| POST | `/patients/{patient_id}/decision` | Persist approve or reject decision. |
| GET | `/patients/{patient_id}/report` | Return approved report or pending/rejected status. |
| GET | `/care-plans` | Return saved generated care plans. |
| POST | `/chat` | Return chat answer and tool-call audit trail. |

### 3.3 Data Interfaces

The system shall read or produce:

- Synthetic FHIR JSON bundles under `data/samples` and
  `data/samples_inpatient`.
- Processed discharge records CSV under `data/processed`.
- Discharge notes JSONL under `data/processed`.
- Local ChromaDB vector store under `data/processed/chroma_db`.
- Experiment registry under `results/experiments.csv`.
- Saved audit, care-plan, fairness, decision, prediction, and report artifacts
  under `results` and `reports`.

### 3.4 External Services

The MVP may call:

- Groq OpenAI-compatible chat completions for reasoning, critique, dynamic
  retrieval category generation, and chat.
- Local FastAPI risk API from the agent risk tool.
- ChromaDB local embedding functionality.

Production EHR, notification, identity, and managed database integrations are
planned and are not required for the local MVP.

## 4. Functional Requirements

### 4.1 Ingestion and Canonical Records

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-001 | The system shall parse synthetic FHIR bundles into canonical discharge records. | Must | Run `python scripts/export_records.py`. |
| SRS-FR-002 | The system shall cluster inpatient encounters into hospitalization episodes. | Must | Unit tests or script validation for `src/ingestion/episodes.py`. |
| SRS-FR-003 | The system shall compute length of stay, medication count, high-risk medication flags, prior admissions, admission reason, patient name, and protected attributes. | Must | Inspect `data/processed/discharge_records.csv`. |
| SRS-FR-004 | The system shall export discharge note text separately from structured model features. | Must | Inspect `data/processed/discharge_notes.jsonl`. |
| SRS-FR-005 | The system shall apply temporal filters so features use information available at discharge. | Must | Tests for `src/ingestion/temporal_filters.py`. |

### 4.2 Target Labeling

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-006 | The system shall label 30-day readmission outcomes using a configurable horizon. | Must | Run `python -m src.features.target`. |
| SRS-FR-007 | The system shall distinguish POSITIVE, NEGATIVE, PLANNED, DEATH, and EXCLUDED outcomes. | Must | Check output summary and target CSV. |
| SRS-FR-008 | The system shall exclude planned readmission patterns from positive outcome labels. | Must | Tests for planned-pattern logic. |
| SRS-FR-009 | The system shall track deaths within the readmission window separately from negatives. | Must | Tests for competing-risk handling. |

### 4.3 Model Training and Registry

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-010 | The system shall train a Cox proportional hazards baseline model. | Must | Run `python -m src.model.train_baseline`. |
| SRS-FR-011 | The system shall compare Cox, Random Survival Forest, and Gradient Boosting Survival Analysis candidates with patient-grouped cross-validation. | Should | Run `python -m src.model.compare_models`. |
| SRS-FR-012 | The system shall log experiment metadata to `results/experiments.csv`. | Must | Run `python -m src.model.run_experiment`. |
| SRS-FR-013 | The system shall save model artifacts by run ID under `models`. | Must | Verify saved `.joblib` artifact. |
| SRS-FR-014 | The system shall load the configured production run ID from `config.yaml`. | Must | Start API and call `/model-info`. |

### 4.4 Fairness and Drift Monitoring

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-015 | The system shall run a subgroup fairness audit for sex and race. | Must | Run `python -m src.model.fairness_audit`. |
| SRS-FR-016 | The system shall report fairness as inconclusive when subgroup event counts are too small. | Must | Inspect fairness audit output. |
| SRS-FR-017 | The system shall log served predictions for drift monitoring. | Should | Call `/predict` and inspect prediction log. |
| SRS-FR-018 | The system shall return a drift report only when enough recent predictions exist to support it. | Should | Call `/drift-report`. |

### 4.5 Model Serving API

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-019 | The API shall build the `/predict` request schema from the loaded model feature list. | Must | Call OpenAPI docs or inspect generated schema. |
| SRS-FR-020 | The API shall return risk score, percentile, category, model run ID, and disclaimer. | Must | POST `/predict`. |
| SRS-FR-021 | The API shall categorize percentiles >= 80 as high, >= 50 as medium, and lower as low. | Must | Unit test `_categorize`. |
| SRS-FR-022 | The API shall require `X-API-Key` for all non-health endpoints. | Must | Request endpoint with and without header. |
| SRS-FR-023 | The API shall attach or echo `X-Request-ID` for request tracing. | Should | Inspect response headers. |
| SRS-FR-024 | The API shall return safe error responses for unexpected exceptions. | Must | Error handling test. |

### 4.6 Retrieval

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-025 | The system shall split discharge notes using clinical section headers. | Must | Tests for `src/embeddings/chunking.py`. |
| SRS-FR-026 | The system shall build a persistent ChromaDB collection from discharge-note chunks. | Must | Run `python -m src.embeddings.build_vector_store`. |
| SRS-FR-027 | The system shall restrict retrieval to the requested patient. | Must | Run `scripts/test_scoped_retrieval.py`. |
| SRS-FR-028 | The system shall apply relevance thresholding so unrelated chunks are not forced into results. | Should | Retrieval tests for no-match cases. |
| SRS-FR-029 | The system shall suppress highly similar retrieved chunks. | Should | Retrieval tests with near-duplicate content. |
| SRS-FR-030 | The retrieval agent shall return category-organized context summaries and no-match categories. | Must | Run `python -m src.agents.retrieval_agent <patient_id>`. |

### 4.7 Agent Orchestration

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-031 | The orchestrator shall call the live risk assessment tool first. | Must | Test `src.agents.orchestrator`. |
| SRS-FR-032 | The orchestrator shall skip retrieval, reasoning, and critique for low risk patients. | Must | Branch test with mocked low risk result. |
| SRS-FR-033 | The orchestrator shall run retrieval, reasoning, and critique for medium and high risk patients. | Must | Branch test with mocked high risk result. |
| SRS-FR-034 | Dynamic retrieval category generation shall fall back to fixed categories on missing API key or malformed output. | Should | Tests for fallback paths. |
| SRS-FR-035 | Reasoning shall draft plans from risk context and retrieved patient context. | Must | Agent integration test. |
| SRS-FR-036 | Critique shall receive the same risk context as reasoning. | Must | Unit test state passed to critique node. |
| SRS-FR-037 | Agent setup failures shall produce actionable errors rather than silent placeholder output. | Must | Missing dependency tests. |

### 4.8 Chat Agent

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-038 | The chat agent shall expose `assess_readmission_risk` and `search_patient_chart` tools to the LLM. | Must | Inspect returned tool calls from `/chat`. |
| SRS-FR-039 | The chat response shall include answer text and tool-call audit trail. | Must | POST `/chat`. |
| SRS-FR-040 | The chart search tool shall guide the model toward validated clinical query phrasings. | Should | Prompt/tool schema review. |

### 4.9 Clinician Review Workflow

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-041 | The API shall cache full assessments per patient episode for a configurable TTL. | Should | Repeated assessment request test. |
| SRS-FR-042 | The API shall save generated care plans to local JSONL. | Should | Inspect `results/care_plans.jsonl`. |
| SRS-FR-043 | The API shall persist approve or reject decisions per patient ID and discharge timestamp. | Must | Tests for `results/decisions.sqlite3`. |
| SRS-FR-044 | Decision writes shall be idempotent per patient episode. | Must | Run decision idempotency test. |
| SRS-FR-045 | Approved decisions shall allow report generation. | Must | GET report after approval. |
| SRS-FR-046 | Rejected decisions shall return edit-required status and no final report. | Must | GET report after rejection. |

### 4.10 Web Application

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| SRS-FR-047 | The web app shall display the risk queue. | Must | Manual UI test. |
| SRS-FR-048 | The web app shall display patient evidence grouped by retrieval category and source. | Must | Manual UI test with assessment payload. |
| SRS-FR-049 | The web app shall allow approve and reject actions from the dashboard. | Must | Manual UI test and API decision record. |
| SRS-FR-050 | The web app shall display approved report preview or rejected edit-required status. | Should | Manual UI test. |
| SRS-FR-051 | The web app shall display chat answers and visible tool-call audit trail. | Should | Manual UI test. |
| SRS-FR-052 | The web app shall show loading, empty, and error states with request IDs when available. | Should | Manual UI test. |

## 5. Non Functional Requirements

### 5.1 Safety

| ID | Requirement |
| --- | --- |
| SRS-NFR-001 | The system shall state that the MVP is not validated for clinical use. |
| SRS-NFR-002 | The system shall state that Cox risk scores are relative scores, not calibrated probabilities. |
| SRS-NFR-003 | The system shall keep clinician approval mandatory before report generation. |
| SRS-NFR-004 | The system shall use synthetic data only for demo operation. |

### 5.2 Security

| ID | Requirement |
| --- | --- |
| SRS-NFR-005 | Non-health API endpoints shall require an API key. |
| SRS-NFR-006 | The system shall not commit secret `.env` values. |
| SRS-NFR-007 | Production versions shall replace demo API-key auth with real identity and RBAC. |

### 5.3 Reliability

| ID | Requirement |
| --- | --- |
| SRS-NFR-008 | API health shall report missing runtime dependencies. |
| SRS-NFR-009 | LLM and risk API calls shall use configurable timeouts. |
| SRS-NFR-010 | Unexpected API errors shall return controlled responses with request IDs. |
| SRS-NFR-011 | Production versions shall add retries and circuit breakers for external integrations. |

### 5.4 Performance

| ID | Requirement |
| --- | --- |
| SRS-NFR-012 | The risk queue shall use precomputed startup scores rather than recomputing every row per request. |
| SRS-NFR-013 | Assessment generation shall be cacheable by patient episode. |
| SRS-NFR-014 | Retrieval shall over-fetch and filter results while preserving patient scope. |

### 5.5 Maintainability

| ID | Requirement |
| --- | --- |
| SRS-NFR-015 | The `/predict` request schema shall derive from the model artifact feature list. |
| SRS-NFR-016 | Configurable pipeline values shall live in `config.yaml` or environment variables. |
| SRS-NFR-017 | Model runs shall be reproducible through registry metadata and saved artifacts. |
| SRS-NFR-018 | Tests shall cover request IDs, API observability, transition reports, and decision idempotency. |

### 5.6 Observability

| ID | Requirement |
| --- | --- |
| SRS-NFR-019 | The system shall log audit events for assessment generation, chat completion, decisions, and reports. |
| SRS-NFR-020 | The system shall log live predictions for drift monitoring. |
| SRS-NFR-021 | The UI shall surface request IDs in error messages when available. |

## 6. Configuration Requirements

The system shall support:

- `model.production_run_id` in `config.yaml`.
- API authentication via `API_KEY`.
- Web API authentication via `VITE_API_KEY`.
- API base URL and frontend request timeouts.
- LLM timeout via `LLM_TIMEOUT_SECONDS`.
- Risk API timeout via `RISK_API_TIMEOUT_SECONDS`.
- Assessment cache TTL via `CACHE_TTL_SECONDS`.
- Groq model and API key variables for reasoning and critique.

## 7. Acceptance Test Matrix

| Scenario | Expected Result |
| --- | --- |
| Start API with configured model and data present. | `/health` returns ok or degraded with dependency details. |
| Request protected endpoint without key. | API returns 401 with request ID. |
| Predict with valid feature payload. | API returns risk score, percentile, category, model run ID, and disclaimer. |
| Generate assessment for low risk episode. | Orchestrator returns templated low risk summary and skips full review. |
| Generate assessment for high risk episode. | Orchestrator returns retrieval context, draft plan, and critique notes. |
| Approve an assessment. | Decision stored and report endpoint returns approved Markdown report. |
| Reject an assessment. | Decision stored and report endpoint returns edit-required status. |
| Ask a chart/risk question. | Chat response includes answer and tool calls. |
| Build frontend. | React build succeeds. |

## 8. Traceability to Proposal

| Proposal Concept | Repository Implementation |
| --- | --- |
| ML layer predicts 30-day readmission risk. | `src/model`, `src/features`, `src/api/main.py` |
| Single agent retrieves chart context with sources. | `src/retrieval`, `src/agents/retrieval_agent.py` |
| Multi-agent workflow drafts and critiques plan. | `src/agents/orchestrator.py`, `reasoning_agent.py`, `critique_agent.py` |
| Clinician review before action. | React dashboard and decision APIs |
| Fairness checked, not assumed. | `src/model/fairness_audit.py`, API disclaimer |
| Safe demo with synthetic data. | Synthea samples and processed synthetic records |

