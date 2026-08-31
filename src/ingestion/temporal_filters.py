"""
Pulls a patient's active conditions and medications as of a discharge date,
using time-window logic rather than a strict encounter reference.

Why: only 31.4% of inpatient encounters in the raw json files have a Condition resource directly
linked via `.encounter`, and only 43.1% have a directly-linked
MedicationRequest (confirmed against the full generated dataset, Day 1).
A patient's real diagnosis and medication history is frequently attached to
an earlier encounter, not the current hospital stay — filtering by direct
link alone silently drops most of it.
"""

from datetime import datetime


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def active_conditions_at_discharge(patient_conditions: list[dict], discharge_date: str) -> list[dict]:
    discharge_dt = _parse_dt(discharge_date)
    active = []
    for c in patient_conditions:
        onset = c.get("onsetDateTime")
        if not onset:
            continue
        onset_dt = _parse_dt(onset)
        if onset_dt > discharge_dt:
            continue  # diagnosed after this stay — leakage, exclude
        abatement = c.get("abatementDateTime")
        if abatement and _parse_dt(abatement) < discharge_dt:
            continue  # resolved before this stay — not active at discharge
        active.append(c)
    return active


def active_medications_at_discharge(patient_medication_requests: list[dict], discharge_date: str, lookback_days: int = 90) -> list[dict]:
    """
    `status="completed"` in Synthea means a finished course (e.g. an antibiotic
    run), not "was ever prescribed" — without a recency bound, a patient's
    entire lifetime of completed prescriptions accumulates (confirmed: one
    47-encounter patient hit 855 "active" medications with no window applied).
    A 90-day lookback approximates "what they were actually on around this
    admission" without needing a MedicationRequest end date, which Synthea
    doesn't reliably populate.
    """
    discharge_dt = _parse_dt(discharge_date)
    valid_status = {"active", "completed"}
    active = []
    for m in patient_medication_requests:
        if m.get("status") not in valid_status:
            continue
        authored = m.get("authoredOn")
        if not authored:
            continue
        authored_dt = _parse_dt(authored)
        if authored_dt > discharge_dt:
            continue  # prescribed after this stay — leakage, exclude
        days_before = (discharge_dt - authored_dt).total_seconds() / 86400
        if days_before > lookback_days:
            continue  # too old to represent current medication burden
        active.append(m)
    return active