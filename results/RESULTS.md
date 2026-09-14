# Project Results Summary - Latest Commit Work

## Baseline Model

- Cox proportional hazards, 6 features (age, LOS, medication count,
  prior admissions, diuretic flag, anticoagulant flag)
- Test C-index: 0.684-0.733 depending on split (see src/model/compare_models.py
  for cross-validated comparison: Cox mean 0.736, beats RSF/GBS)
- comorbidity_count and two medication flags (insulin, opioid) were tested
  and excluded — each showed a sign reversal vs. raw data, confirmed via
  correlation analysis (see scripts/check_multicollinearity.py)

## Fairness Audit

- Status: **Inconclusive** for both sex and race at current dataset size
- Only 1 subgroup per attribute (female / White) has enough events (≥5)
  to compute a reliable metric
- Not a negative result — the audit infrastructure works correctly and
  refuses to report numbers it can't support. Revisit once the population
  is scaled up further.
- See results/fairness_audit_*.txt for full timestamped run logs. 