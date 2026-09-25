# Product Requirements Document

# Care Transition Copilot

## 1. Purpose

Care Transition Copilot is a clinician-facing decision support application for
post-discharge care coordination. It predicts relative 30-day readmission risk,
retrieves relevant patient chart context, drafts a follow-up care plan, and
requires clinician review before any plan is treated as approved.

The product exists to close the operational gap between risk identification and
follow-up action. A readmission risk score alone does not reduce avoidable
readmissions unless care teams can understand the patient context, draft a
reasonable plan, and act on it inside a controlled review workflow.

## 2. Background

The original project proposal defines the system as a layered ML, agent, and
agentic AI workflow:

- A prediction model identifies patients at elevated 30-day readmission risk.
- A retrieval agent gathers patient-specific clinical context with source
  citations.
- A multi-agent workflow drafts and critiques a follow-up plan before clinician
  review.
- Approved plans can be handed off to the patient record and follow-up workflow.

The current repository implements the local MVP using synthetic Synthea data,
structured feature engineering, a survival-model risk API, ChromaDB-backed
retrieval, Groq-hosted reasoning and critique agents, a FastAPI backend, and a
React clinician UI.

## 3. Goals

The product should:

1. Identify discharged patients with elevated relative readmission risk.
2. Present risk scores with clear caveats and traceable patient context.
3. Draft patient-specific follow-up plans grounded in retrieved chart evidence.
4. Run draft plans through a critique step before clinician review.
5. Require explicit clinician approval or rejection before report generation.
6. Preserve auditability for risk predictions, agent tool calls, generated care
   plans, and clinician decisions.
7. Support a safe local demo using synthetic data only.

## 4. Non Goals

The current MVP does not:

1. Provide clinically validated readmission probabilities.
2. Replace clinician judgment.
3. Write directly to a production EHR.
4. Send real patient notifications.
5. Provide production identity, RBAC, or managed persistence.
6. Claim a passed fairness audit at the current sample size.
7. Use real patient data.

## 5. Target Users

### Primary Users

- Discharge coordinators who prioritize follow-up work.
- Clinicians reviewing AI-drafted care transition plans.
- Care management staff who need a concise view of patient risk and chart
  evidence.

### Secondary Users

- Data scientists monitoring model behavior and fairness.
- Engineers operating the API, retrieval index, and web app.
- Demo evaluators reviewing the end-to-end workflow.

## 6. User Problems

| Problem | Product Response |
| --- | --- |
| Risk scores and care plans live in separate systems. | Bring risk scoring, retrieval, drafting, critique, and review into one workflow. |
| Care teams need to know why a patient was flagged. | Show risk category, percentile, admission reason, and retrieved evidence. |
| LLM-generated care plans may overstate or invent facts. | Constrain drafting to retrieved context and run a separate critique step. |
| Staff need to approve or reject drafts explicitly. | Persist clinician decisions per patient episode. |
| Demo workflows must avoid real patient data. | Use synthetic Synthea data and public mock reports only. |

## 7. Product Scope

### Current MVP Scope

The current MVP includes:

- Synthetic FHIR ingestion and discharge episode construction.
- 30-day readmission target generation with exclusions for planned
  readmissions and deaths.
- Cox proportional hazards risk modeling with experiment tracking.
- Model comparison across Cox, Random Survival Forest, and Gradient Boosting
  Survival Analysis candidates.
- Inconclusive but explicit fairness audit reporting for sex and race.
- FastAPI risk-model serving with API-key protection.
- Drift report endpoint backed by logged predictions.
- Section-aware discharge-note chunking.
- ChromaDB vector-store indexing and patient-scoped retrieval.
- Risk-gated LangGraph orchestration.
- Dynamic retrieval category generation for medium and high risk patients.
- Groq-hosted care-plan drafting and critique.
- Free-form clinician chat with auditable tool calls.
- React UI with risk queue, Patients, Care plans, Ask a question, approve and
  reject actions, and approved mock report preview.
- JSONL audit logs, saved care-plan logs, and SQLite-backed decision storage.

### Planned Scope

Planned production hardening includes:

- Managed relational persistence instead of local JSONL and SQLite files.
- Production OAuth2, SSO, and role-based access control.
- Full edit and resubmission workflow for rejected care plans.
- FHIR write-back stubs and later production EHR integration.
- Notification stubs for portal, SMS, or reminder workflows.
- CI/CD, managed observability, retries, and circuit breakers.
- Larger synthetic population or approved validation dataset for stronger
  fairness analysis.
- Better independence between reasoning and critique models.

## 8. User Experience Requirements

### Risk Queue

The risk queue should show recently discharged patients, risk category, risk
percentile, admission reason, and enough context for care teams to prioritize
review.

### Patient Evidence

The patient evidence view should show the retrieved chart excerpts used by the
agent. Evidence should be grouped by retrieval category and source, with
duplicate and irrelevant sections suppressed where possible.

### Draft Care Plan

The draft care plan should remain visibly draft-only until a clinician acts.
The UI should show critique notes, decision status, actor label, and report
status.

### Clinician Decision

A clinician should be able to approve or reject a generated plan. Approved
plans can generate a mock Markdown care-transition report. Rejected plans should
be marked for edit and resubmission.

### Ask a Question

The chat view should allow ad hoc questions about a patient. When the assistant
uses tools, the UI should expose the tool calls and results as an audit trail.

## 9. Functional Requirements Summary

| ID | Requirement |
| --- | --- |
| PRD-FR-001 | The system shall ingest synthetic FHIR patient bundles and export canonical discharge records. |
| PRD-FR-002 | The system shall generate a 30-day readmission target with planned, death, excluded, positive, and negative outcomes. |
| PRD-FR-003 | The system shall serve a configured risk model through an API. |
| PRD-FR-004 | The system shall categorize risk as low, medium, or high using percentile thresholds. |
| PRD-FR-005 | The system shall retrieve patient-scoped chart context from embedded discharge notes. |
| PRD-FR-006 | The system shall draft care plans for medium and high risk patients using retrieved context. |
| PRD-FR-007 | The system shall critique draft care plans before clinician review. |
| PRD-FR-008 | The system shall skip full chart review for low risk patients and return a templated summary. |
| PRD-FR-009 | The system shall persist clinician approve or reject decisions by patient episode. |
| PRD-FR-010 | The system shall generate mock transition reports only for approved decisions. |

## 10. Success Metrics

### Product Metrics

- High risk patients can be identified and reviewed from one workflow.
- Clinicians can approve or reject draft plans from the UI.
- Approved reports are generated only after a persisted approval.
- Chat and care-plan outputs expose source or tool-call context.

### Model and Retrieval Metrics

- Risk model C-index remains comparable to local baseline results.
- Fairness audit is reported with honest sample-size caveats.
- Retrieved chart context cites the correct patient and encounter metadata.
- Retrieval avoids forcing unrelated chunks when a patient has no relevant
  documentation.

### Operational Metrics

- API health reports missing dependencies.
- Request IDs connect UI errors to server logs.
- Drift report becomes meaningful after enough logged predictions.
- Audit logs capture assessments, chat completions, decisions, and reports.

## 11. Constraints and Assumptions

- The MVP uses synthetic data only.
- The risk score is a relative Cox model score, not an absolute probability.
- Fairness status is inconclusive at the current event count.
- Reasoning and critique require configured Groq credentials.
- ChromaDB local embedding setup may need network access on first build.
- Local results and report files are demo artifacts, not production records.

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| Users may interpret risk scores as calibrated probabilities. | Every API response includes a disclaimer; UI copy should preserve the distinction. |
| Low event count can make model and fairness results unstable. | Report cross-validation variance and fairness inconclusiveness explicitly. |
| LLM may generate unsupported care-plan statements. | Ground drafting in retrieved context and run critique before review. |
| Retrieval may miss relevant note content. | Use section-aware chunking, patient-scoped search, validated query phrasings, and manual checks. |
| Demo persistence is not production-grade. | Keep local JSONL and SQLite in MVP scope and plan managed storage. |

## 13. Release Criteria

The MVP is acceptable for local demo when:

1. The API starts with the configured production model.
2. `/health`, `/model-info`, `/patients`, `/predict`, and `/drift-report` work
   with API-key requirements respected.
3. A medium or high risk patient can run through assessment, retrieval, plan
   drafting, critique, and clinician decision.
4. A low risk patient receives a summary without unnecessary LLM workflow.
5. Approved decisions generate reports and rejected decisions do not.
6. The React app can show the risk queue, patients, care plans, chat, and
   decision status.

