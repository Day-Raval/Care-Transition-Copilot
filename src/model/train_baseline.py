"""
Baseline Cox proportional hazards model for 30-day readmission risk.

Trains only on POSITIVE/NEGATIVE episodes (DEATH and EXCLUDED are dropped —
see target.py for why). Uses a patient-grouped train/test split, not a plain
random one: 99+ patients in this dataset contribute multiple episodes, and
a random split could put two episodes from the same patient on both sides,
which would leak patient identity into the test evaluation and inflate the
apparent C-index.
"""

import logging
import sys

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit
from sksurv.linear_model import CoxPHSurvivalAnalysis
from sksurv.metrics import concordance_index_censored

sys.path.insert(0, ".")
from src.utils.config import load_config
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)

COMORBIDITY_CATEGORIES = [
    "heart_failure", "diabetes", "copd", "renal_disease", "hypertension",
    "coronary_artery_disease", "obesity", "substance_use", "cancer",
]
# comorbidity_count dropped entirely: confirmed entangled with FOUR
# other features simultaneously — age_at_discharge (r=0.719, very strong),
# medication_count (r=0.489), med_flag_diuretic (r=0.414), and
# prior_admissions_90d (r=0.353). Unlike med_flag_insulin/opioid (each
# correlated with one thing, cleanly dropped), this one has no single
# "redundant partner" to remove instead — its signal is already distributed
# across age, medication burden, and admission history, which are already
# in the model. With only ~52 events, there isn't enough data to assign it
# independent credit, and its coefficient stayed negative (HR ~0.745,
# clinically implausible) across every prior fix. See scripts/check_comorbidity.py.

MED_FLAG_COLUMNS = ["med_flag_diuretic", "med_flag_anticoagulant"]

NUMERIC_FEATURES = ["age_at_discharge", "length_of_stay_days", "medication_count", "prior_admissions_90d"]

BINARY_FEATURES = set(MED_FLAG_COLUMNS)


def prepare_features(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, pd.Series]:
    """
    Returns (X, y_structured, patient_groups).

    X: numeric features + medication-class flags. comorbidity_count and
       admission_reason are both intentionally excluded — see comments
       above and at NUMERIC_FEATURES/MED_FLAG_COLUMNS for why.

    y_structured: the sksurv "structured array" format Cox models expect —
       (event_observed: bool, days_observed: float) per row.

    patient_groups: patient_id per row, for the grouped train/test split.
    """
    modeling_df = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])].copy()

    feature_cols = NUMERIC_FEATURES + MED_FLAG_COLUMNS
    X = modeling_df[feature_cols].copy()
    for c in MED_FLAG_COLUMNS:
        X[c] = X[c].astype(int)

    y_structured = np.array(
        list(zip(modeling_df["event_observed"], modeling_df["days_observed"])),
        dtype=[("event", "bool"), ("time", "float64")],
    )

    return X, y_structured, modeling_df["patient_id"]


def train_test_split_grouped(X, y, groups, test_size=0.25, random_state=42):
    splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state)
    train_idx, test_idx = next(splitter.split(X, y, groups=groups))
    return X.iloc[train_idx], X.iloc[test_idx], y[train_idx], y[test_idx], groups.iloc[train_idx], groups.iloc[test_idx]


def fit_and_evaluate(X_train, y_train, X_test, y_test):
    model = CoxPHSurvivalAnalysis(alpha=1.0)  # stronger L2 penalty than the
                                                # first attempt (0.1) — needed
                                                # given ~52 events across
                                                # even the trimmed feature set
    model.fit(X_train, y_train)

    risk_scores_test = model.predict(X_test)
    c_index = concordance_index_censored(y_test["event"], y_test["time"], risk_scores_test)[0]

    risk_scores_train = model.predict(X_train)
    c_index_train = concordance_index_censored(y_train["event"], y_train["time"], risk_scores_train)[0]

    return model, c_index, c_index_train


def interpret_c_index(c_index: float) -> str:
    """Plain-language read of what the number actually means."""
    pct = c_index * 100
    if c_index >= 0.9:
        quality = "excellent"
    elif c_index >= 0.8:
        quality = "strong"
    elif c_index >= 0.7:
        quality = "acceptable — usable as a first baseline, not production-ready"
    elif c_index >= 0.6:
        quality = "weak — better than a coin flip, but not by much"
    else:
        quality = "close to random guessing"
    return (
        f"In plain terms: if you picked one patient who WAS readmitted and one who "
        f"WASN'T at random, the model correctly identifies the readmitted patient as "
        f"higher-risk about {pct:.0f}% of the time. That's {quality}."
    )


def print_coefficients(model: CoxPHSurvivalAnalysis, feature_names: list[str]) -> None:
    """
    The Cox-native equivalent of a SHAP summary: for a linear model like this,
    the coefficients ARE the feature importances, in units that are directly
    interpretable (exp(coef) = hazard ratio). No approximation needed.

    Adds a plain-language sentence per feature — a hazard ratio on its own
    ("1.51") doesn't mean anything to most readers without translating it
    into "X% higher/lower risk."
    """
    coefs = model.coef_
    hazard_ratios = np.exp(coefs)
    order = np.argsort(-np.abs(coefs))

    print(
        "\nHow to read this: a hazard ratio above 1.0 means the feature is "
        "associated with HIGHER readmission risk; below 1.0 means LOWER risk. "
        "1.0 exactly would mean no effect. Each estimate holds all other "
        "features constant (e.g. the age effect is 'for patients with the "
        "same medication count, comorbidity count, etc.')."
    )

    print(f"\n{'Feature':<30} {'Coefficient':>12} {'Hazard Ratio':>14}")
    print("-" * 60)
    for i in order:
        name = feature_names[i]
        coef = coefs[i]
        hr = hazard_ratios[i]
        print(f"{name:<30} {coef:>12.3f} {hr:>14.3f}")

    print("\nPlain-language interpretation, ranked by strength of effect:")
    for i in order:
        name = feature_names[i]
        hr = hazard_ratios[i]
        pct_change = abs(hr - 1) * 100
        direction = "higher" if hr > 1 else "lower" if hr < 1 else "no different"

        if name in BINARY_FEATURES:
            subject = f"Patients on {name.replace('med_flag_', '')} medication"
            comparison = "than patients not on one"
        else:
            subject = f"Each additional point of {name}"
            comparison = "holding other factors constant"

        if hr == 1:
            print(f"  - {subject}: no meaningful difference in risk.")
        else:
            print(f"  - {subject} have about {pct_change:.0f}% {direction} readmission risk, {comparison}.")


if __name__ == "__main__":
    setup_logging()
    cfg = load_config()

    target_path = cfg.output_csv.replace(".csv", "_with_target.csv")
    df = pd.read_csv(target_path)

    X, y, groups = prepare_features(df)
    print(f"Modeling set: {len(X)} episodes from {groups.nunique()} unique patients")
    print(f"Features: {len(X.columns)}")
    print(f"Positive rate: {100*y['event'].mean():.1f}%")
    print(f"(Only {y['event'].sum()} total positive events — keep that in mind when reading any result below.)\n")

    X_train, X_test, y_train, y_test, groups_train, groups_test = train_test_split_grouped(X, y, groups)

    overlap = set(groups_train) & set(groups_test)
    assert not overlap, f"Patient leakage across split! {len(overlap)} patients in both train and test"
    print(f"Train: {len(X_train)} episodes / {groups_train.nunique()} patients")
    print(f"Test:  {len(X_test)} episodes / {groups_test.nunique()} patients")
    print(f"Confirmed: zero patient overlap between train and test\n")

    model, c_index_test, c_index_train = fit_and_evaluate(X_train, y_train, X_test, y_test)

    print(f"C-index (train): {c_index_train:.3f}")
    print(f"C-index (test):  {c_index_test:.3f}")
    print(interpret_c_index(c_index_test))

    print_coefficients(model, list(X.columns))