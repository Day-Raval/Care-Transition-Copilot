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
from src.utils.runtime import RISK_API_TIMEOUT_SECONDS

API_BASE_URL = os.getenv("RISK_API_BASE_URL", "http://localhost:8080")


def get_patient_features(patient_id: str, discharge_ts: str | None = None) -> dict:
    cfg = load_config()
    df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))
    patient_rows = df[df["patient_id"] == patient_id]
    if patient_rows.empty:
        raise ValueError(f"No episode found for patient_id={patient_id} in the processed dataset.")

    if discharge_ts is not None:
        patient_rows = patient_rows[patient_rows["discharge_ts"].astype(str) == discharge_ts]
        if patient_rows.empty:
            raise ValueError(f"No episode found for patient_id={patient_id} discharge_ts={discharge_ts}.")

    row = patient_rows.sort_values("discharge_ts").iloc[-1]

    return {
        "age_at_discharge": int(row["age_at_discharge"]),
        "length_of_stay_days": int(row["length_of_stay_days"]),
        "medication_count": int(row["medication_count"]),
        "prior_admissions_90d": int(row["prior_admissions_90d"]),
        "med_flag_diuretic": bool(row["med_flag_diuretic"]),
        "med_flag_anticoagulant": bool(row["med_flag_anticoagulant"]),
    }, str(row.get("admission_reason", "unknown")), str(row.get("patient_name", "Unknown Patient"))


def assess_risk(patient_id: str, discharge_ts: str | None = None) -> dict:
    features, admission_reason, patient_name = get_patient_features(patient_id, discharge_ts)
    api_key = os.getenv("API_KEY")
    headers = {"X-API-Key": api_key} if api_key else {}

    try:
        response = requests.post(f"{API_BASE_URL}/predict", json=features, headers=headers, timeout=RISK_API_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Could not reach the Model Serving API at {API_BASE_URL}. "
            f"Start it first:\n  uvicorn src.api.main:app --port 8080"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"Timed out waiting for the Model Serving API at {API_BASE_URL} "
            f"after {RISK_API_TIMEOUT_SECONDS:.0f} seconds."
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Risk API request failed: {e}")

    result = response.json()
    result["admission_reason"] = admission_reason
    result["patient_name"] = patient_name
    return result


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python3 -m src.agents.risk_tool <patient_id>")
        sys.exit(1)

    result = assess_risk(sys.argv[1])
    print(json.dumps(result, indent=2))
