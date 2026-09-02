"""
The canonical shape every parsed inpatient encounter gets flattened into.
Everything downstream (feature engineering, the risk model, the agents)
reads from this, not from raw FHIR.
"""

from dataclasses import dataclass, field


@dataclass
class DischargeRecord:
    patient_id: str
    encounter_id: str
    admit_ts: str
    discharge_ts: str
    length_of_stay_days: int
    age_at_discharge: int
    encounter_count: int  # how many raw IMP encounters were merged into this episode

    admission_reason: str                     # Encounter.reasonCode — 100% populated, confirmed
    discharge_disposition: str | None          # only ~10% populated — must allow None

    comorbidity_categories: list[str] = field(default_factory=list)
    medication_count: int = 0
    high_risk_med_flags: dict[str, bool] = field(default_factory=dict)
    prior_admissions_90d: int = 0

    # Evaluation-only — never joined into the training feature matrix
    protected_attributes: dict[str, str] = field(default_factory=dict)

    discharge_note_text: str | None = None