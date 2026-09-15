"""
Tracks live prediction requests and compares their distribution against
the training reference distribution, to catch two different problems:

1. DATA DRIFT — are incoming patients' features starting to look
   different from the population the model was trained on?
2. PREDICTION DRIFT — are the model's own output risk scores drifting
   away from what they looked like on the training population? This can
   happen even without obvious single-feature drift, if features shift
   in combination.

Uses a two-sample Kolmogorov-Smirnov test per feature (and for the
prediction distribution) — a standard way to check whether two samples
come from the same underlying distribution without assuming a shape
upfront.

Gated on a minimum sample size (MIN_PREDICTIONS_FOR_DRIFT_CHECK), same
principle as the fairness audit's MIN_EVENTS_FOR_AUDIT: a drift check run
on 5 live predictions isn't a real signal, and reporting one anyway would
be worse than reporting nothing.
"""

import csv
import os
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

PREDICTION_LOG_PATH = "results/prediction_log.csv"
MIN_PREDICTIONS_FOR_DRIFT_CHECK = 30
DRIFT_P_VALUE_THRESHOLD = 0.05


def log_prediction(feature_values: dict, risk_score: float):
    """Appends one served prediction's inputs + output to a durable log,
    so drift can be assessed across restarts, not just this process's
    lifetime."""
    os.makedirs(os.path.dirname(PREDICTION_LOG_PATH), exist_ok=True)
    file_exists = os.path.exists(PREDICTION_LOG_PATH)

    row = {"timestamp": datetime.now().isoformat(timespec="seconds"), "risk_score": risk_score, **feature_values}

    with open(PREDICTION_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def load_recent_predictions(n: int = 200):
    if not os.path.exists(PREDICTION_LOG_PATH):
        return None
    df = pd.read_csv(PREDICTION_LOG_PATH)
    return df.tail(n)


def compute_drift_report(reference_df: pd.DataFrame, reference_scores: np.ndarray, feature_names: list[str]) -> dict:
    """
    reference_df: the training feature matrix (same one used to build the
        percentile reference at API startup)
    reference_scores: the training risk score distribution
    Returns a dict describing per-feature and prediction drift, or a
    "not enough data yet" status if fewer than MIN_PREDICTIONS_FOR_DRIFT_CHECK
    live predictions have been logged.
    """
    recent = load_recent_predictions()
    if recent is None or len(recent) < MIN_PREDICTIONS_FOR_DRIFT_CHECK:
        n = 0 if recent is None else len(recent)
        return {
            "status": f"NOT ENOUGH LIVE TRAFFIC — {n}/{MIN_PREDICTIONS_FOR_DRIFT_CHECK} predictions logged. "
                       f"Drift cannot be assessed reliably yet.",
            "n_recent_predictions": n,
            "feature_drift": {},
            "prediction_drift": None,
        }

    feature_drift = {}
    for feat in feature_names:
        if feat not in recent.columns:
            continue
        stat, p_value = ks_2samp(reference_df[feat].astype(float), recent[feat].astype(float))
        feature_drift[feat] = {
            "ks_statistic": round(float(stat), 4),
            "p_value": round(float(p_value), 4),
            "drifted": bool(p_value < DRIFT_P_VALUE_THRESHOLD),
        }

    pred_stat, pred_p = ks_2samp(reference_scores, recent["risk_score"].astype(float))
    prediction_drift = {
        "ks_statistic": round(float(pred_stat), 4),
        "p_value": round(float(pred_p), 4),
        "drifted": bool(pred_p < DRIFT_P_VALUE_THRESHOLD),
    }

    any_drifted = prediction_drift["drifted"] or any(f["drifted"] for f in feature_drift.values())
    status = "DRIFT DETECTED" if any_drifted else "NO SIGNIFICANT DRIFT DETECTED"

    return {
        "status": status,
        "n_recent_predictions": len(recent),
        "feature_drift": feature_drift,
        "prediction_drift": prediction_drift,
    }