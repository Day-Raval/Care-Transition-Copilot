"""
Risk Model Tool — connects the agent pipeline to the FastAPI Model
Serving API (src/api/main.py). Closes the actual gap in this project:
the risk model has been served via a real API since the "Model Serving
API" build, and the agent pipeline (retrieval/reasoning/critique) has
been built separately since — the two systems have never called each
other until now.

Looks up the patient's model features from the processed dataset,
POSTs them to the running API's /predict endpoint, and returns the
result. Requires the API to be running — fails loudly with the exact
command to start it, rather than silently skipping risk assessment and
treating every patient as low-risk by default.
"""

import os
import sys

import pandas as pd
import requests

sys.path.insert(0, ".")
from src.utils.config import load_config

API_BASE_URL = os.getenv("RISK_API_BASE_URL", "http://localhost:8080")


def get_patient_features(patient_id: str) -> dict:
    """
    Looks up this patient's most recent episode's features from the
    processed dataset. Raises ValueError if the patient isn't found —
    fail loudly rather than silently defaulting to some placeholder
    feature set.
    """
    cfg = load_config()
    df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))
    patient_rows = df[df["patient_id"] == patient_id]
    if patient_rows.empty:
        raise ValueError(f"No episode found for patient_id={patient_id} in the processed dataset.")

    row = patient_rows.sort_values("discharge_ts").iloc[-1]

    return {
        "age_at_discharge": int(row["age_at_discharge"]),
        "length_of_stay_days": int(row["length_of_stay_days"]),
        "medication_count": int(row["medication_count"]),
        "prior_admissions_90d": int(row["prior_admissions_90d"]),
        "med_flag_diuretic": bool(row["med_flag_diuretic"]),
        "med_flag_anticoagulant": bool(row["med_flag_anticoagulant"]),
    }, str(row.get("admission_reason", "unknown"))


def assess_risk(patient_id: str) -> dict:
    """
    Calls the live Model Serving API. Raises RuntimeError with the exact
    fix if the API isn't reachable — this should fail loudly, not
    silently proceed as if the patient were low-risk.
    """
    features, admission_reason = get_patient_features(patient_id)

    try:
        response = requests.post(f"{API_BASE_URL}/predict", json=features, timeout=10)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Could not reach the Model Serving API at {API_BASE_URL}. "
            f"Start it first:\n  uvicorn src.api.main:app --port 8080"
        )

    result = response.json()
    result["admission_reason"] = admission_reason
    return result


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python3 -m src.agents.risk_tool <patient_id>")
        sys.exit(1)

    result = assess_risk(sys.argv[1])
    print(json.dumps(result, indent=2))