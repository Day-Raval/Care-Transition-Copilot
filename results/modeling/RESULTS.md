# Project Results Summary

## Where things stand

Phase 1 (synthetic data → risk model → fairness check) is complete.
Everything below is backed by an actual run you can reproduce, not a
plan — every number here came from `results/experiments.csv` or a
script in `src/model/`.

---

## Data pipeline (Days 1-5)

- **Source:** Synthea-generated synthetic patients, seed 42, ~3,000
  target population (`config.yaml` → `synthea.population_size`)
- **Unit of analysis:** hospitalization *episodes*, not raw FHIR
  encounters — Synthea sometimes splits one real hospital stay into
  multiple encounter resources; these are merged (`src/ingestion/episodes.py`)
  before anything downstream sees them
- **Target definition** (`src/features/target.py`): 30-day readmission,
  with two corrections found through investigation, not assumed upfront:
  - **PLANNED exclusion** — ~89% of the first-pass "readmissions" turned
    out to be the same lung cancer staging code repeating within days of
    itself (a Synthea oncology-module artifact, not a real unplanned
    bounce-back). Excluded using the same logic CMS uses to exclude
    planned readmissions from its own readmission measure.
  - **DEATH as a competing risk** — patients who died within the 30-day
    window are tracked separately, not folded into "no readmission."
  - Final breakdown at ~3,110 episodes: **POSITIVE 52 (1.7%)**, NEGATIVE
    2,593 (83.4%), DEATH 60 (1.9%), PLANNED 380 (12.2%), EXCLUDED 25 (0.8%)

---

## Day 6 — Baseline Model

**Model: Cox Proportional Hazards, `alpha=1.0`, 6 features.**

### How the final feature set was decided

Started with 8-9 candidate features. Three were removed, each backed by
a specific, confirmed reason — not a guess:

| Feature removed | Why |
|---|---|
| `med_flag_insulin` | Correlated with `medication_count` (r=0.446) and `comorbidity_count` (r=0.361). Its Cox coefficient said "decreases risk," but the raw unadjusted rate showed insulin patients were **41% more likely** to be readmitted — a sign reversal caused by collinearity, not a real finding. |
| `med_flag_opioid` | Same pattern. Correlated with `medication_count` (r=0.370). Raw rate: **62% more likely** to be readmitted; model said "decreases risk." |
| `comorbidity_count` | Entangled with **four** other features at once — `age_at_discharge` (r=0.719, very strong), `medication_count` (r=0.489), `med_flag_diuretic` (r=0.414), `prior_admissions_90d` (r=0.353). Tested via 5-fold cross-validation whether keeping it was worth the loss of interpretability: it added only **+0.014 mean C-index** — within the fold-to-fold noise (std ~0.085), not a real improvement. |

**Final features:** `age_at_discharge`, `length_of_stay_days`,
`medication_count`, `prior_admissions_90d`, `med_flag_diuretic`,
`med_flag_anticoagulant`

### Model comparison

Tested Cox against Random Survival Forest and Gradient Boosting, 5-fold
grouped cross-validation (`src/model/compare_models.py`):

| Model | Mean C-index | Std |
|---|---|---|
| **Cox (alpha=5.0)** | **0.736** | 0.084 |
| Cox (alpha=1.0, chosen) | 0.732 | 0.085 |
| Best RSF | 0.721 | 0.072 |
| Best GBS | 0.691 | 0.091 |

Cox won outright — both tree-ensemble families trailed, confirming the
expected pattern at this event count (~52 total): complex models overfit
small samples rather than finding real extra signal.

### Final baseline result

- **C-index (test): 0.684–0.733**, depending on split (single-split
  results vary more than cross-validated ones at this event count —
  treat the CV mean as the trustworthy number)
- Plain-language: correctly ranks a readmitted patient as higher-risk
  than a non-readmitted one roughly **70-73% of the time**
- All 6 remaining coefficients point the clinically expected direction —
  `prior_admissions_90d` is the strongest signal (HR ~1.4-1.6), as
  expected from the clinical literature

### Experiment tracking

Every training run is logged to `results/experiments.csv` (features,
hyperparameters, split sizes, C-index) with the model file saved
alongside it in `models/{run_id}.joblib` — see
`src/model/run_experiment.py` and `src/model/compare_experiments.py`.
The registry includes an automatic overfitting flag (train/test gap
> 0.15) so a high single-run score isn't mistaken for a real win without
cross-validated confirmation.

---

## Day 7 — Fairness Audit

**Status: Inconclusive for both sex and race, at current dataset size.**

Built and validated (`src/model/fairness_audit.py`): checks group-wise
C-index and false-negative-rate parity at a real decision threshold (top
20% highest risk = "flagged for care management"), gated on a minimum
5-event-per-group threshold to avoid reporting numbers computed from a
handful of cases.

| Attribute | Result |
|---|---|
| Sex | Only `female` (9 test events) cleared the threshold; `male` (3 events) did not. Cannot compare. |
| Race | Only `White` (10 test events) cleared the threshold. All other categories: 0-2 events. Cannot compare. |

**This is not a "the model passed" result.** It means the audit
genuinely cannot determine fairness or unfairness yet — there isn't
enough data in the minority groups. **Do not describe this model as
"fairness-audited" or "bias-checked"** in any writeup or demo without
this caveat attached. Revisit once the population is scaled well beyond
~3,000 patients — rough estimate, 8,000-10,000+ needed before race-based
comparison becomes possible at the current ~2% event rate.

---

## What's tracked where

| File/folder | Contents |
|---|---|
| `config.yaml` | All tunable pipeline parameters (population size, episode gap threshold, medication lookback, readmission horizon) |
| `data/processed/discharge_records_with_target.csv` | The modeling dataset — one row per episode, with the final target columns |
| `results/experiments.csv` | Every logged training run, comparable side by side |
| `models/*.joblib` | Saved model files, one per logged run (gitignored — regenerable) |
| `results/decisions.sqlite3` | Local approve/reject decisions for generated care plans (gitignored) |
| `results/care_plans.jsonl` / `results/audit_log.jsonl` | Local saved care-plan and audit-event logs; database mode routes these records to SQL tables instead |
| `results/fairness_audit_*.txt` | Timestamped fairness audit reports |

---

## Known limitations (carry forward, don't re-discover)

1. **Comorbidity categorization is keyword-based**, not a real SNOMED
   crosswalk (`src/ingestion/comorbidity.py`) — confirmed necessary because
   Synthea emits 100% SNOMED CT, not ICD-10, so standard Elixhauser/Charlson
   mappings don't apply directly. Fine for this stage, would need
   replacing before anything clinical-facing.
2. **Fairness audit is inconclusive**, not passed (see above).
3. **Event count is thin** (~52) even after scaling to ~3,000 patients —
   every result in this document should be read as "directional for a
   first baseline," not "final and precise."

---

## Vector Store — Section-Aware Chunking (resolved)
Original whole-note embedding diluted background/comorbidity mentions
(e.g. CHF history) when they weren't the encounter's chief complaint —
confirmed via scripts/check_chf_notes.py (108 notes mentioned heart
failure, none surfaced for a direct CHF query). Fixed via section-aware
chunking (split by note headers: Chief Complaint, HPI, Plan, etc.),
Preamble removal (100% redundant with discharge_ts metadata), and merging
of trivially short sections that were winning matches by being generic
rather than relevant.

Confirmed working: chest pain and CHF-procedure queries now surface
genuinely relevant content. Patient-scoped retrieval (src/retrieval/
query_store.py) adds a relevance distance threshold (1.1, uncalibrated —
revisit with real usage) so a query with no genuine match returns "no
relevant history found" rather than forcing out an unrelated chunk —
confirmed correct on a patient with no CHF history. Positive-case
confirmation (a real CHF patient correctly surfacing relevant content)
is the one remaining validation step before the retrieval agent build.

--- 

## Agent Orchestration — Retrieval, Reasoning, Critique (LangGraph)

**Status: Working end to end, validated across multiple patients.**

Built the three-agent pipeline from the original architecture's Agent
layer: Retrieval Agent -> Reasoning Agent -> Plan Critique Agent,
orchestrated with LangGraph. Confirmed working both individually and
chained together automatically via the orchestrator.

### Retrieval Agent (`src/agents/retrieval_agent.py`)
Patient-scoped semantic search over discharge notes (`src/retrieval/query_store.py`,
built on ChromaDB), organized into 5 fixed clinical categories (admission
reason, comorbidities, medications, procedures/plan, follow-up) rather
than one free-text query. No LLM dependency — deliberately kept
testable and free of an external API for this step.

Validated against real patients with the real embedding model:
correctly surfaces relevant chart content (e.g. a genuine cardiac
workup — echocardiography, troponin, heart failure tracking panel —
for a patient with confirmed CHF history), and correctly returns
`(no relevant documentation found)` for categories with genuinely
nothing on file, rather than forcing a weak match. Relevance threshold
(1.3) calibrated against one confirmed real true positive.

**Known limitation, not investigated further:** some patients show
duplicate/near-duplicate chunks in retrieval results (e.g. the same
chief complaint appearing twice). Confirmed cosmetic — does not appear
to affect downstream draft quality in testing — but the root cause
(duplicate encounters vs. an indexing artifact) was not conclusively
determined.

### Reasoning Agent (`src/agents/reasoning_agent.py`)
Drafts a 3-section follow-up care plan (risk factors, recommended
actions, additional review notes) from the retrieved context, using Groq
(`openai/gpt-oss-120b`). Prompt explicitly forbids inventing
medications/diagnoses/procedures not in the retrieved context, and
requires unsupported or low-confidence areas to be handled as review notes
rather than hidden or overstated.

Confirmed via testing: correctly grounds claims in retrieved content
(e.g. "no active medications" reflected accurately); correctly hedges
on ambiguous source data rather than fabricating precision (a patient's
notes showed two different ages, 59 and 67, across encounters — the
draft used ">60 years" rather than picking one arbitrarily); and now
presents extra reviewer-facing caveats as "Additional review notes"
instead of raw documentation-gap language.

### Plan Critique Agent (`src/agents/critique_agent.py`)
Independent second-model review before a clinician would see the draft,
using a different model (`openai/gpt-oss-20b`) on the same Groq account.
Checks for hallucination, clinical overreach (specific dosing/diagnosis
decisions), and whether limited chart support is handled carefully in
additional review notes.

**Real reliability issue found and fixed during validation:** initial
testing (4 runs across 2 patients) showed the critique agent was
inconsistent — the same type of routine recommendation (e.g. "schedule
a PCP visit," "refer to neurology if symptoms persist") was flagged as
"hallucination" in one run and correctly passed in another, on
functionally identical draft content. Root cause: the prompt didn't
distinguish "asserting an undocumented fact as true" from "recommending
a future action grounded in a real documented symptom." Fixed by adding
explicit positive/negative examples of each to the prompt. Confirmed
fixed via repeat testing: the same two patients now get consistent,
correct verdicts across multiple runs — PASS on legitimate
recommendations, still correctly flags genuine overreach (a prior run
caught the draft introducing "neuropathic symptoms" as a specific
clinical term not present in the source, which only said "tingling in
hands and feet").

**Note on model independence:** both reasoning and critique currently
run on OpenAI's open-weight models (120B and 20B) — different sizes,
not different companies. This is a real, known limitation on how
independent the "second opinion" actually is; worth revisiting if a
genuinely different-company model becomes available and confirmed
working on the project's Groq account.

### Orchestrator (`src/agents/orchestrator.py`)
LangGraph state machine wiring all three agents: retrieval -> reasoning
-> critique. State-passing and node execution order verified via
streaming. Each node fails loudly with a clear setup message if its
required API key is missing, rather than silently producing placeholder
output.

Confirmed working end to end on 2 patients with materially different
chart profiles (a thin-documentation case and a data-rich oncology case)
— both produced grounded drafts with appropriate review notes and correct,
non-arbitrary critique verdicts.

## Agent Workflow — Risk Model Integration & Dynamic Retrieval

**Status: Working end to end, validated across multiple patients with
materially different clinical profiles (cardiac vs. oncology).**

Closed a real gap that existed until this point: the Model Serving API
(risk model) and the agent pipeline (retrieval/reasoning/critique) had
been built as two separate systems that never called each other.

### Risk Model Tool (`src/agents/risk_tool.py`)
Connects the agent pipeline to the live Model Serving API over HTTP.
Looks up a patient's 6 model features from the processed dataset, calls
`/predict`, and returns the risk assessment plus the patient's actual
admission reason (used downstream for dynamic retrieval). Fails loudly
with the exact fix (`uvicorn src.api.main:app --port 8080`) if the API
isn't running, rather than silently treating every patient as low-risk.

### Conditional Routing (`src/agents/orchestrator.py`)
The orchestrator now makes a genuine decision based on the real risk
score: LOW-risk patients get a fast, templated summary and skip
retrieval/reasoning/critique entirely; MEDIUM/HIGH-risk patients get the
full pipeline. Confirmed via mocked testing that both branches execute
the correct, and only the correct, sequence of nodes.

### Dynamic Category Generation (`src/agents/retrieval_agent.py`)
Replaced the fixed 5 generic retrieval categories with LLM-generated
ones, tailored per-patient to their actual admission reason and risk
level (via `generate_dynamic_categories()`). Falls back automatically to
the fixed 5 on any failure (missing API key, malformed LLM output) —
confirmed via testing that both failure modes correctly fall back rather
than leaving retrieval empty.

Confirmed working on two clinically distinct patients: a cardiac patient
generated categories like "prosthetic valve function" and "cardiac
biomarkers... troponin, BNP"; an oncology patient generated "histopathology
and molecular profiling," "EGFR, ALK, ROS1, KRAS, PD-L1 results" — genuinely
different, clinically appropriate query sets, not generic labels reworded.

### Bug found and fixed during validation
Critique agent was receiving the reasoning agent's draft plan but NOT
the risk-assessment context that draft was built from. Result: the
critique agent flagged the risk percentile itself as an unsupported
claim ("the chart does not provide a percentile ranking") on every
medium/high-risk patient — a systematic false positive that would have
affected every future run, not a one-off. Fixed by passing the same
risk-context line to both reasoning and critique via a shared
`_risk_context_line()` helper, plus an explicit prompt instruction
telling the critique model this line is pre-validated, not part of the
chart to scrutinize. Confirmed fixed via direct before/after comparison
on the same patient, same risk score — FLAGGED became PASS with no other
changes.

### Known limitation, not investigated further
Some patients' retrieved chunks show heavy internal repetition (e.g. the
same medication line appearing a dozen+ times within one chunk) when a
patient's real documentation is genuinely limited to one repetitive
source (e.g. a chemo regimen logged across many similar encounters).
Confirmed cosmetic — the reasoning agent correctly summarized the
repeated content once rather than restating it — but the underlying
duplicate-chunk question (flagged earlier, also not chased down) likely
explains this pattern and remains open.

## Chat Agent — Real LLM Tool-Calling (#6)

**Status: Working, validated across multiple query types, two real bugs
found and fixed during testing.**

Built `src/agents/chat_agent.py` and `src/agents/tools.py` as a second,
genuinely different interface alongside the deterministic orchestrator.
Where the orchestrator always runs the same fixed sequence (risk ->
retrieval -> reasoning -> critique), this agent uses real Groq
function-calling: an LLM decides, per question, whether to call
`assess_readmission_risk`, `search_patient_chart`, both, or neither.

Both interfaces are intentionally kept — the orchestrator remains the
right tool for "a discharge just happened, produce an auditable draft
plan" (deterministic, easy to test); the chat agent is for ad hoc
questions a clinician might ask, where a fixed pipeline would be either
too heavy (for a simple lookup) or too rigid (for an open-ended question
needing a mix of sources).

Every tool call, from either interface, goes through the exact same
audited functions (`risk_tool.assess_risk`,
`query_store.retrieve_relevant_context`) — the chat agent's flexibility
doesn't come at the cost of a separate, unaudited code path. Every call
is logged (name, arguments, result) and returned alongside the answer.

### Two real bugs found and fixed during validation

1. **Vague query phrasing caused false negatives.** The `search_patient_chart`
   tool originally let the model choose any free-text query. On an
   open-ended question, it chose `"discharge summary"` — a phrase that
   doesn't match how notes are actually sectioned — got a genuine "no
   relevant documentation found," and then incorrectly told the user the
   chart lacked medication information that earlier testing had already
   confirmed *was* retrievable with the right phrasing. Fixed by
   constraining the tool's query description to the 5 phrasings already
   validated throughout this project's retrieval work (see
   `retrieval_agent.py`'s `RETRIEVAL_CATEGORIES`).

2. **A prompt-editing mistake dropped a required instruction.** An
   intermediate fix (nudging the model toward multiple targeted chart
   searches) accidentally omitted the earlier instruction to always call
   `assess_readmission_risk` for open-ended questions. Result: the agent
   answered "no obvious high-risk medical problem" for a patient
   objectively at the 90th percentile on the trained model — a
   confidently wrong conclusion, not an honest gap. Caught via the same
   before/after re-test discipline used throughout this project, fixed
   by combining both instructions into one complete prompt block.

### Confirmed final behavior
- Narrow risk question -> only `assess_readmission_risk` called
- Narrow chart question -> only `search_patient_chart` called, with a
  validated query phrasing
- Open-ended "should I be worried" question -> both tools called,
  multiple targeted chart searches, correct risk category stated,
  recommendations grounded in actual retrieved content rather than
  generic boilerplate
- Consistently surfaces real documentation inconsistencies (e.g. a
  medication list that contradicts the same note's "No Active
  Medications" line) without being specifically prompted to

## Production Hardening and Clinician Web App

**Status: First production-readiness pass complete for the local MVP.**

This pass focused on making the demo safer to run, easier to debug, and more
usable through the browser without changing the underlying model. It does not
make the system clinically production-ready, but it closes several operational
gaps that were previously obvious in local testing.

### API safeguards (`src/api/main.py`, `src/api/production.py`)
- Added centralized safe handling for unexpected API exceptions. Users now get a
  controlled error message while the server logs the traceback.
- Added dependency-aware `/health` output so setup problems such as missing
  processed data, vector store, production run id, or Groq key are visible.
- Added required API-key protection for local demo mode through `API_KEY`
  and the `X-API-Key` request header.
- Added episode-specific assessment support through the optional `discharge_ts`
  query parameter, so repeat patients are not collapsed to patient ID alone.
- Added an assessment cache controlled by `CACHE_TTL_SECONDS` to avoid
  regenerating the same LLM-backed assessment on every dashboard click.
- Added file-based audit logging to `results/audit_log.jsonl`.
- Added file-based saved care plans to `results/care_plans.jsonl`.
- Added SQLite-backed care-plan decision storage in `results/decisions.sqlite3`.
- Added optional SQL-backed persistence for decisions, saved care plans, and
  audit events when `PERSISTENCE_BACKEND=database` and `DATABASE_URL` are set.
- Added request tracing with `X-Request-ID`, echoed by the API and shown in web
  error messages so demo failures can be matched to server logs.
- Added `GET /care-plans` to inspect saved generated plans.
- Added `GET` and `POST /patients/{patient_id}/decision` for retrieving and
  recording the latest approve/reject decision for a patient episode.
- Made care-plan decisions idempotent per `patient_id + discharge_ts`, with
  actor tracking (`demo_clinician` in API-key mode; token subject in OIDC mode).
- Added approved mock care-transition report generation through
  `GET /patients/{patient_id}/report`. Approved reports are saved under
  `reports/` using public demo filenames such as
  `care-transition-report__2026-08-09__994d6249.md`; rejected drafts return an
  edit-required status instead of a finalized report.

### Runtime controls (`src/utils/runtime.py`, agent modules)
- Added configurable timeouts for Groq calls via `LLM_TIMEOUT_SECONDS`.
- Added configurable timeout handling for the internal risk API call via
  `RISK_API_TIMEOUT_SECONDS`.
- Updated risk-tool failures so API connection and timeout failures produce
  actionable messages rather than low-level request errors.

### React clinician UI (`web/`)
- Enabled the previously disabled **Patients** and **Care plans** sections.
- Added a Patients table backed by the existing `/patients` API.
- Added a Care plans page that selects a patient and generates/views the draft
  follow-up plan.
- Added frontend request timeout handling via `VITE_REQUEST_TIMEOUT_MS` and
  API-key header support via `VITE_API_KEY`.
- Added reusable loading/error/empty states for web pages.
- Removed Synthea's numeric suffixes from patient names in display only.
- Removed the dashboard fairness banner from the main workflow while preserving
  fairness caveats in API/model documentation.

### Evidence and answer presentation
- Chat answers now render as formatted notes instead of raw markdown-looking
  text.
- Dashboard patient evidence now shows the actual cited chart snippets used by
  the agent, grouped by retrieval category and source.
- Not-found-only retrieval sections are hidden from the chart-excerpt panel.
- The plan's third section is now branded as **Additional review notes** rather
  than "Documentation gaps" or "Chart information not found."
- Raw "no relevant documentation found" lines are suppressed in the UI.

## Latest UI Validation - Patient Evidence Panel

**Status: Fixed and validated against real project data.**

The patient-evidence panel had four concrete display defects in production
output: duplicated section headers, repeated clinical line items, incorrectly
split hyphenated clinical terms, and run-on text where unrelated retrieved
content was concatenated without a separator. The fix was kept in the React
display layer (`web/src/components/Dashboard.jsx`) so retrieval behavior remains
unchanged and auditable.

### What was fixed

| Defect | Fix |
|---|---|
| Category header repeated inside its own body | Strip body lines that match the rendered section header before display |
| Clinical bullets treated as new cited excerpts | Only `- [Source] ...` starts a new retrieved excerpt; inner `- item` bullets stay inside that excerpt |
| Consecutive duplicate procedures, labs, and medications | Deduplicate rendered items within each evidence block |
| Terms such as `basic metabolic 2000 panel - serum or plasma` split into separate bullets | Stop treating every spaced hyphen as a list boundary; preserve hyphenated clinical terms |
| Plan, lab, medication, and care-plan content running together | Split retrieved content into labeled sub-blocks: Procedures, Lab Reports, Medications, Care Plan |

### Real-data validation

Validated with live `/patients/{id}/assessment` responses from the local API and
with exact real discharge-note excerpts from `data/processed/discharge_notes.jsonl`.

| Validation case | Result |
|---|---|
| Live assessment with Plan-section procedure bullets | Plan bullets now remain under `SOURCE: Plan` instead of being misclassified as separate source rows |
| Live assessment with repeated Social History excerpts across generated categories | Repeated excerpts collapse to one displayed block |
| Live assessment with both procedure and care-plan content | Procedures and care-plan entries render under separate subheaders |
| Discharge-note excerpt with repeated labs/medications and a hyphenated lab term | Repeated labs/medications collapse, while `basic metabolic 2000 panel - serum or plasma` remains one bullet |

Confirmed build: `npm.cmd run build`.

## Latest Workflow Validation - Assessment Cache and Decisions

**Status: Backend decision path wired for approve/reject.**

The dashboard now loads assessments by patient episode (`patient_id` plus
`discharge_ts`) and fetches any existing clinician decision alongside the
assessment. This fixes the prior limitation where a repeated patient could be
treated as one undifferentiated case in the review flow.

### What was added

| Addition | Current behavior |
|---|---|
| API key auth | All non-health endpoints require `X-API-Key` matching `API_KEY` |
| Assessment cache | Generated assessments are cached per patient episode for `CACHE_TTL_SECONDS`; set to `0` to disable |
| Episode-specific assessment | `/patients/{patient_id}/assessment` accepts `discharge_ts` |
| Decision read/write | `/patients/{patient_id}/decision` supports latest-decision lookup and approve/reject writes |
| Decision persistence | Decisions are stored locally in `results/decisions.sqlite3` with patient ID, discharge timestamp, decision, decided time, actor, and draft plan |
| Decision idempotency | Repeated approve/reject writes update the same episode decision instead of inserting duplicates |
| Report export | Approved decisions generate Markdown care-transition reports in `reports/`; rejected decisions show edit-required status |
| Dashboard actions | Approve and Reject are active buttons; saved decisions render as status badges with actor labels and report preview/status |

Edit remains intentionally unwired in this pass. Rejected drafts are clearly
marked for edit and resubmission, but the current backend stores only final
`approved` or `rejected` decisions, not edited plan text.

## Latest Persistence Update - Database Backend and Clinician Attribution

**Status: Optional SQL persistence path added and covered by tests.**

The local MVP still defaults to JSONL audit/care-plan logs plus
`results/decisions.sqlite3`, but persistence can now be switched to a SQL
database by setting `PERSISTENCE_BACKEND=database` and `DATABASE_URL`. The same
API functions write decision records, saved care plans, and audit events in
either mode, so the web/API layer does not need a separate code path.

### What was added

| Addition | Current behavior |
|---|---|
| Persistence backend switch | `PERSISTENCE_BACKEND=local` keeps the original JSONL/SQLite demo path; `database`, `postgres`, or `postgresql` enables SQLAlchemy-backed storage |
| Database tables | Database mode creates `care_plan_decisions`, `care_plans`, and `audit_events` tables |
| Decision upsert | Approve/reject decisions remain idempotent per `patient_id + discharge_ts` in both local and database modes |
| Saved care-plan reads | `GET /care-plans` now reads from the selected persistence backend instead of assuming JSONL |
| Clinician attribution | The web client sends `X-Clinician-ID` and includes the same actor in decision writes, defaulting to `demo_clinician` |
| Health check | `/health` now reports whether `DATABASE_URL` is present when database persistence is requested |
| Tests | `tests/test_decision_idempotency.py` covers local SQLite idempotency plus database-backed decision and care-plan persistence |

This persistence pass added the storage switch first; the later OIDC pass below
adds authenticated user attribution and role checks at the API boundary.

## Latest Security and Runtime Update - OIDC, Redis Cache, and Postgres Tools

**Status: Security/runtime hardening added for the local MVP.**

The API can now run in either local API-key mode or OIDC user-access mode.
`AUTH_MODE=api_key` keeps the original shared-key demo path. `AUTH_MODE=oidc`
requires bearer access tokens signed with the configured JWKS, issuer, audience,
subject, expiry, and roles claim. Route-level role checks gate patient data,
care plans, chat, decisions, approved reports, drift reports, and model
metadata. The `/predict` endpoint remains service-to-service in OIDC mode and
still requires the shared `API_KEY`.

### What was added

| Addition | Current behavior |
|---|---|
| OIDC verification | `src/api/security.py` validates RS256 access tokens using `OIDC_ISSUER`, `OIDC_AUDIENCE`, and `OIDC_JWKS_URL` |
| Role-based route checks | `care_coordinator`, `clinician`, `data_scientist`, and `admin` roles authorize only the matching API actions |
| OIDC web client | The React app can run with `VITE_AUTH_MODE=oidc`, sign users in/out, and attach bearer tokens to API requests |
| Decision attribution | API-key mode keeps `X-Clinician-ID`; OIDC mode records the verified token subject as the actor |
| Redis assessment cache | Setting `REDIS_URL` stores episode assessment cache entries in Redis using `CACHE_TTL_SECONDS`; Redis failures fall back to the in-process cache |
| Postgres migration tooling | `database/check_PostgresConnection.py`, `database/migrate_to_postgres.py`, and `database/verify_migration.py` move and verify local JSONL/SQLite/CSV data in Postgres |
| Tests | `tests/test_api_security.py` covers OIDC token validation, role enforcement, and decision attribution behavior |

This is still not full clinical production readiness. It proves the app boundary
can enforce identity and roles, but deployment-specific identity-provider setup,
managed secrets, monitoring, and incident operations remain future work.

## Next steps

1. ~~Wrap the chosen model in a FastAPI service~~ — **Done.**
2. ~~Begin the agent orchestration layer~~ — **Done.**
3. ~~Connect the risk model to the agent pipeline~~ — **Done.**
4. Revisit the fairness audit once population size increases — still open.
5. ~~Investigate the duplicate/repeated-chunk pattern~~ — **Done.** Both
   symptoms traced to real, non-bug root causes (genuine dense clinical
   documentation; genuinely different encounters with similar Synthea-
   templated text). Added a text-similarity diversity filter as a
   retrieval-quality improvement.
6. ~~Risk model as an LLM-callable tool~~ — **Done.** See "Chat Agent"
   section above.
7. Address the reasoning/critique model independence gap — still open
   (both currently OpenAI open-weight models, different sizes only).
8. ~~Clinician Web App (React UI — risk queue dashboard, patient list,
   care-plan view, chat)~~ — **First pass done.**
9. ~~Apply first production-grade hardening pass~~ — **Done.** Added safe API
   errors, health dependency checks, request timeouts, API-key protection,
   audit logging, saved care-plan records, assessment caching, and decision
   storage.
10. ~~Fix cited-context display formatting and deduplication in the dashboard~~
   — **Done.** Validated against live assessment payloads and real discharge-note
   excerpts.
11. ~~Wire clinician approve/reject actions to real backend state~~ — **Done.**
   Edit remains open.
12. ~~Add a database-backed persistence path for decisions, saved care plans,
   and audit events~~ - **Done.** Managed deployment hardening is still open.
13. ~~Add true authentication/RBAC~~ - **First pass done.** OIDC bearer-token
   verification and role checks are implemented; production identity-provider
   setup and operations hardening remain open.
14. Next up: add FHIR write-back/notification stubs and harden managed
   deployment operations.
