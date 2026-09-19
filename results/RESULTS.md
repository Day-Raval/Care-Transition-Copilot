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
actions, documentation gaps) from the retrieved context, using Groq
(`openai/gpt-oss-120b`). Prompt explicitly forbids inventing
medications/diagnoses/procedures not in the retrieved context, and
requires documentation gaps to be surfaced, not glossed over.

Confirmed via testing: correctly grounds claims in retrieved content
(e.g. "no active medications" reflected accurately); correctly hedges
on ambiguous source data rather than fabricating precision (a patient's
notes showed two different ages, 59 and 67, across encounters — the
draft used ">60 years" rather than picking one arbitrarily); correctly
lists genuine documentation gaps when they exist.

### Plan Critique Agent (`src/agents/critique_agent.py`)
Independent second-model review before a clinician would see the draft,
using a different model (`openai/gpt-oss-20b`) on the same Groq account.
Checks for hallucination, clinical overreach (specific dosing/diagnosis
decisions), and glossed-over documentation gaps.

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
chart profiles (a thin-documentation case with 2 explicit "no relevant
documentation found" categories, and a data-rich oncology case) — both
produced grounded drafts with correctly surfaced gaps and correct,
non-arbitrary critique verdicts.

### Bugs found and fixed along the way
- `retrieve_relevant_context()` was missing `encounter_id` in its return
  dict, crashing `retrieval_agent.py` on every call
- Critique agent was initially built against the wrong provider
  (Anthropic) based on outdated project planning docs — corrected to
  Groq once the current `.env.example` clarified both models should be
  Groq-hosted
- Default model names (`llama-3.3-70b-versatile` for reasoning) had been
  deprecated/restricted on the current Groq account — replaced with
  confirmed-working models (`openai/gpt-oss-120b` / `openai/gpt-oss-20b`)
- Critique prompt inconsistency — fixed with explicit examples in system prompts

## Next steps

1. ~~Wrap the chosen model in a FastAPI service~~ — **Done.** Model
   Serving API built (`src/api/main.py`), adaptive request schema,
   drift monitoring, fairness disclaimer on every prediction. See
   "Model Serving API" section above.
2. ~~Begin the agent orchestration layer~~ — **Done.** Retrieval,
   reasoning, and critique agents built and validated, orchestrated via
   LangGraph. See "Agent Orchestration" section above.
3. Revisit the fairness audit once population size increases —
   **still open.** Only the majority group per protected attribute
   (female for sex, White for race) has enough test-set events to audit
   reliably at current dataset size (~3,110 episodes, ~52 positive
   events). Estimated 8,000-10,000+ patients needed for a determinate
   race-based comparison.
4. Investigate the duplicate-chunk pattern in retrieval results (flagged
   as a known cosmetic limitation, not chased down) — determine whether
   it's genuine duplicate encounters or an indexing artifact.
5. Address the reasoning/critique model independence gap — both
   currently run on OpenAI's open-weight models (120B/20B), same
   company, different sizes. Revisit if a genuinely different-company
   model becomes confirmed-working on the project's Groq account.
6. Wire up the Clinician Web App layer (React UI — risk queue dashboard,
   patient detail/plan review) per the original architecture, once the
   above are addressed or explicitly deprioritized.