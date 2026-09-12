"""
One-off diagnostic — not part of the training pipeline. Run this if you
ever reconsider dropping/re-adding a feature in train_baseline.py, to
check whether it's redundant with (correlated to) features already in
the model.

Confirmed 2026: med_flag_insulin correlated with medication_count (r=0.446)
and comorbidity_count (r=0.361) — both moderate-to-strong. With only ~52
positive events, this collinearity was flipping med_flag_insulin's Cox
coefficient sign (model said "decreases risk"; raw rate says the opposite:
9.6% readmission among insulin patients vs 6.8% among non-insulin). That's
why it was dropped from MED_FLAG_COLUMNS in train_baseline.py.
"""

import sys

import pandas as pd

sys.path.insert(0, ".")
from src.utils.config import load_config

COMORBIDITY_CATEGORIES = [
    "heart_failure", "diabetes", "copd", "renal_disease", "hypertension",
    "coronary_artery_disease", "obesity", "substance_use", "cancer",
]


def interpret_correlation(r: float) -> str:
    """Plain-language read of a correlation coefficient's strength."""
    abs_r = abs(r)
    if abs_r >= 0.7:
        strength = "very strong"
    elif abs_r >= 0.4:
        strength = "moderate-to-strong"
    elif abs_r >= 0.2:
        strength = "weak-to-moderate"
    else:
        strength = "negligible"
    direction = "tend to move together" if r > 0 else "tend to move in opposite directions" if r < 0 else "show no relationship"
    return f"{strength} ({direction})"


def check_correlations(df: pd.DataFrame, flag_column: str) -> None:
    modeling = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])].copy()
    modeling[flag_column] = modeling[flag_column].astype(int)

    for cat in COMORBIDITY_CATEGORIES:
        modeling[f"comorbid_{cat}"] = modeling["comorbidity_categories"].fillna("").str.contains(cat).astype(int)
    modeling["comorbidity_count"] = modeling[[f"comorbid_{c}" for c in COMORBIDITY_CATEGORIES]].sum(axis=1)

    corr_med = modeling[flag_column].corr(modeling["medication_count"])
    corr_comorbid = modeling[flag_column].corr(modeling["comorbidity_count"])

    print(f"Correlation between {flag_column} and other features:")
    print(f"  vs medication_count:   {corr_med:.3f}   -> {interpret_correlation(corr_med)}")
    print(f"  vs comorbidity_count:  {corr_comorbid:.3f}   -> {interpret_correlation(corr_comorbid)}")

    print(
        "\nWhy this matters: if a flag is strongly correlated with another "
        "feature already in the model, a Cox model can't reliably tell which "
        "one deserves credit for their shared effect on risk — especially "
        "with few positive events. The result is often an unstable or even "
        "backwards-looking coefficient for one of the two, not a real finding."
    )

    print(f"\n{flag_column} rate by outcome (the raw, unadjusted picture):")
    rates = {}
    for outcome in ["POSITIVE", "NEGATIVE"]:
        subset = modeling[modeling["outcome"] == outcome]
        rate = subset[flag_column].mean()
        rates[outcome] = rate
        print(f"  {outcome}: {rate*100:.1f}% ({subset[flag_column].sum()}/{len(subset)})")

    if rates["POSITIVE"] > 0 and rates["NEGATIVE"] > 0:
        relative_diff = (rates["POSITIVE"] - rates["NEGATIVE"]) / rates["NEGATIVE"] * 100
        direction = "MORE" if relative_diff > 0 else "LESS"
        print(
            f"\nPatients with {flag_column}=True were "
            f"{abs(relative_diff):.0f}% {direction} likely to be readmitted "
            f"than patients without it, in the raw data — before any model "
            f"adjustment. Comparing in this direction to what the trained model's "
            f"coefficient says shows: if they disagree, that's the collinearity "
            f"effect described above, not a real reversal of the relationship."
        )


if __name__ == "__main__":
    cfg = load_config()
    target_path = cfg.output_csv.replace(".csv", "_with_target.csv")
    df = pd.read_csv(target_path)

    check_correlations(df, flag_column="med_flag_insulin")
    check_correlations(df, flag_column="med_flag_opioid")