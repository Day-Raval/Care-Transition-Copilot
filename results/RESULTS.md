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

## Next steps

1. Wrap the chosen model (`cox_baseline_v1.joblib` equivalent, logged in
   the experiment registry) in a FastAPI service — the Model Serving API
   layer from the system architecture
2. Revisit the fairness audit once population size increases
3. Begin the agent orchestration layer (retrieval agent, reasoning,
   critique agent) once the API exists for them to call