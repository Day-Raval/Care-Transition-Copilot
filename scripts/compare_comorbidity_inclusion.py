"""
Resolves the one open question left from the feature-cleanup: does
comorbidity_count actually help the model, or was its negative coefficient
(HR ~0.745, "more comorbidities = lower risk") purely a collinearity
artifact worth removing outright?

Where this fits in the story so far:
  1. train_baseline.py originally included comorbidity_count alongside
     medication_count, prior_admissions_90d, and age_at_discharge.
  2. Its coefficient stayed negative and clinically implausible through
     multiple fixes (dropping med_flag_insulin, then med_flag_opioid) —
     ruling out those two as the cause.
  3. check_comorbidity_correlations.py found the real reason: it's
     entangled with FOUR other features at once (age r=0.719, medication
     count r=0.489, diuretic flag r=0.414, prior admissions r=0.353) — not
     one clean redundant partner to swap out, unlike insulin/opioid.
  4. Dropping it from train_baseline.py fixed every coefficient's direction
     (all point the expected way now) but the test C-index dropped from
     0.733 to 0.684 on that one split — meaning the feature may carry real
     predictive signal even though its own coefficient can't be trusted.

This script settles that trade-off properly: instead of trusting one
train/test split (noisy with only ~52 events), it runs 5-fold grouped
cross-validation with and without comorbidity_count and compares the
AVERAGE performance, the same evaluation approach compare_models.py uses
for comparing model families.

The result of this script is what decides whether comorbidity_count goes
back into train_baseline.py's NUMERIC_FEATURES (WITH, but explicitly
documented as "not individually interpretable"), or stays removed (WITHOUT,
if it adds nothing or hurts once averaged across folds).
"""

import sys
import numpy as np
import pandas as pd
from sksurv.linear_model import CoxPHSurvivalAnalysis

sys.path.insert(0, ".")
from src.model.compare_models import cross_validate_grouped
from src.utils.config import load_config

COMORBIDITY_CATEGORIES = [
    "heart_failure", "diabetes", "copd", "renal_disease", "hypertension",
    "coronary_artery_disease", "obesity", "substance_use", "cancer",
]
MED_FLAG_COLUMNS = ["med_flag_diuretic", "med_flag_anticoagulant"]
NUMERIC_FEATURES = ["age_at_discharge", "length_of_stay_days", "medication_count", "prior_admissions_90d"]


def prepare(df, include_comorbidity_count: bool):
    modeling_df = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])].copy()
    feature_cols = list(NUMERIC_FEATURES) + list(MED_FLAG_COLUMNS)

    if include_comorbidity_count:
        for cat in COMORBIDITY_CATEGORIES:
            modeling_df[f"comorbid_{cat}"] = modeling_df["comorbidity_categories"].fillna("").str.contains(cat).astype(int)
        modeling_df["comorbidity_count"] = modeling_df[[f"comorbid_{c}" for c in COMORBIDITY_CATEGORIES]].sum(axis=1)
        feature_cols.append("comorbidity_count")

    X = modeling_df[feature_cols].copy()
    for c in MED_FLAG_COLUMNS:
        X[c] = X[c].astype(int)
    y = np.array(list(zip(modeling_df["event_observed"], modeling_df["days_observed"])),
                 dtype=[("event", "bool"), ("time", "float64")])
    return X, y, modeling_df["patient_id"]


def interpret_comparison(without_scores, with_scores) -> str:
    """
    Plain-language verdict, plus the specific action to take in
    train_baseline.py, so the result of this script maps directly onto
    a code change rather than being left to interpretation.
    """
    without_mean, without_std = np.mean(without_scores), np.std(without_scores)
    with_mean, with_std = np.mean(with_scores), np.std(with_scores)
    diff = with_mean - without_mean

    lines = []
    lines.append(f"WITHOUT comorbidity_count: mean C-index {without_mean:.3f} (std {without_std:.3f})")
    lines.append(f"WITH comorbidity_count:    mean C-index {with_mean:.3f} (std {with_std:.3f})")
    lines.append(f"Difference (WITH - WITHOUT): {diff:+.3f}")
    lines.append("")

    if diff > 0.02 and with_std <= without_std + 0.05:
        lines.append(
            "VERDICT: comorbidity_count helps, consistently, not just on one lucky "
            "split. Recommended action: add it back to NUMERIC_FEATURES in "
            "train_baseline.py, but keep the code comment noting its own "
            "coefficient isn't individually trustworthy (it's entangled with "
            "age/medication count/prior admissions) — it's earning its place "
            "by improving the model's overall risk ranking, not by being a "
            "feature you can quote a hazard ratio for on its own."
        )
    elif diff < -0.02:
        lines.append(
            "VERDICT: comorbidity_count hurts once averaged across folds — the "
            "single-split drop wasn't a fluke, it's real. Recommended action: "
            "leave it out of train_baseline.py. The 6-feature version is both "
            "more interpretable AND more predictive; there's no trade-off to "
            "justify keeping it."
        )
    else:
        lines.append(
            "VERDICT: the difference is small enough to be within normal "
            "fold-to-fold noise at this event count — not a clear win either "
            "way. Recommended action: leave comorbidity_count out. With a tie "
            "on performance, the simpler, fully-interpretable 6-feature model "
            "is the better default — every coefficient in it can be reported "
            "and trusted on its own, which matters for a clinician-facing tool."
        )
    return "\n".join(lines)


if __name__ == "__main__":
    cfg = load_config()
    df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))

    results = {}
    for label, include in [("WITHOUT comorbidity_count", False), ("WITH comorbidity_count", True)]:
        X, y, groups = prepare(df, include)
        scores = cross_validate_grouped(lambda: CoxPHSurvivalAnalysis(alpha=1.0), X, y, groups, n_splits=5)
        results[include] = scores
        print(f"{label}: mean={np.mean(scores):.3f}  std={np.std(scores):.3f}  folds={[round(s,3) for s in scores]}")

    print()
    print(interpret_comparison(results[False], results[True]))