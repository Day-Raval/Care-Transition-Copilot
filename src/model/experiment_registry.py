"""
A lightweight run registry — not a full experiment-tracking tool like
MLflow (overkill for a project this size), just a plain CSV that answers
"what have I already tried, and which run actually performed best" without
having to re-read old terminal output or retrain to compare.

Each row in results/experiments.csv is one training run: what features/
hyperparameters were used, how big the split was, and how it performed.
Each run's actual model file is saved alongside it in models/, named by
the same run_id, so you can always load back the exact model behind any
row in the table.
"""

import os
import uuid
from datetime import datetime

import joblib
import pandas as pd

EXPERIMENTS_CSV = "results/experiments.csv"
MODELS_DIR = "models"

REGISTRY_COLUMNS = [
    "run_id", "timestamp", "model_type", "alpha", "features",
    "n_features", "n_train", "n_test", "events_train", "events_test",
    "c_index_train", "c_index_test", "notes",
]


def _new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


def log_run(model, feature_names: list[str], alpha: float,
            n_train: int, n_test: int, events_train: int, events_test: int,
            c_index_train: float, c_index_test: float, notes: str = "") -> str:
    """
    Saves the model to models/{run_id}.joblib and appends one row to
    results/experiments.csv. Returns the run_id so the caller can print
    it / reference it.
    """
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(EXPERIMENTS_CSV), exist_ok=True)

    run_id = _new_run_id()
    model_path = os.path.join(MODELS_DIR, f"{run_id}.joblib")
    joblib.dump({"model": model, "feature_names": feature_names}, model_path)

    row = {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model_type": type(model).__name__,
        "alpha": alpha,
        "features": ",".join(feature_names),
        "n_features": len(feature_names),
        "n_train": n_train,
        "n_test": n_test,
        "events_train": events_train,
        "events_test": events_test,
        "c_index_train": round(c_index_train, 4),
        "c_index_test": round(c_index_test, 4),
        "notes": notes,
    }

    if os.path.exists(EXPERIMENTS_CSV):
        df = pd.read_csv(EXPERIMENTS_CSV)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row], columns=REGISTRY_COLUMNS)

    df.to_csv(EXPERIMENTS_CSV, index=False)
    return run_id


def load_model(run_id: str):
    """Loads back the exact model + feature list for a given run_id."""
    path = os.path.join(MODELS_DIR, f"{run_id}.joblib")
    saved = joblib.load(path)
    return saved["model"], saved["feature_names"]


def load_registry() -> pd.DataFrame:
    if not os.path.exists(EXPERIMENTS_CSV):
        return pd.DataFrame(columns=REGISTRY_COLUMNS)
    return pd.read_csv(EXPERIMENTS_CSV)