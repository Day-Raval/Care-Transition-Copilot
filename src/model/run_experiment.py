"""
Trains one Cox model configuration and logs it to the experiment registry
(results/experiments.csv + models/{run_id}.joblib).

Usage:
    python3 -m src.model.run_experiment --alpha 1.0 --notes "baseline, 6 features"
    python3 -m src.model.run_experiment --alpha 2.0 --include-comorbidity --notes "with comorbidity_count"

Every run gets its own row in the registry and its own saved model file —
nothing gets overwritten, so you can always go back and compare or reload
any prior attempt.
"""

import argparse
import sys

import numpy as np
import pandas as pd
from sksurv.linear_model import CoxPHSurvivalAnalysis
from sksurv.metrics import concordance_index_censored

sys.path.insert(0, ".")
from src.model.train_baseline import (
    COMORBIDITY_CATEGORIES, MED_FLAG_COLUMNS, NUMERIC_FEATURES,
    train_test_split_grouped,
)
from src.model.experiment_registry import log_run
from src.utils.config import load_config


def prepare_features_configurable(df: pd.DataFrame, include_comorbidity: bool):
    """Same idea as train_baseline.prepare_features, but with comorbidity_count
    toggleable — so a single script can produce either variant on demand."""
    modeling_df = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])].copy()
    feature_cols = list(NUMERIC_FEATURES) + list(MED_FLAG_COLUMNS)

    if include_comorbidity:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alpha", type=float, default=1.0, help="Cox ridge regularization strength")
    parser.add_argument("--include-comorbidity", action="store_true", help="include comorbidity_count feature")
    parser.add_argument("--notes", type=str, default="", help="free-text note describing this run")
    args = parser.parse_args()

    cfg = load_config()
    df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))

    X, y, groups = prepare_features_configurable(df, args.include_comorbidity)
    X_train, X_test, y_train, y_test, _, _ = train_test_split_grouped(X, y, groups)

    model = CoxPHSurvivalAnalysis(alpha=args.alpha)
    model.fit(X_train, y_train)

    c_index_train = concordance_index_censored(y_train["event"], y_train["time"], model.predict(X_train))[0]
    c_index_test = concordance_index_censored(y_test["event"], y_test["time"], model.predict(X_test))[0]

    run_id = log_run(
        model=model,
        feature_names=list(X.columns),
        alpha=args.alpha,
        n_train=len(X_train), n_test=len(X_test),
        events_train=int(y_train["event"].sum()), events_test=int(y_test["event"].sum()),
        c_index_train=c_index_train, c_index_test=c_index_test,
        notes=args.notes,
    )

    print(f"Run logged: {run_id}")
    print(f"  Features: {list(X.columns)}")
    print(f"  C-index train/test: {c_index_train:.3f} / {c_index_test:.3f}")
    print(f"  Model saved: models/{run_id}.joblib")


if __name__ == "__main__":
    main()