"""
Request/response contracts for the risk model API.

build_patient_features_model() replaces a hardcoded PatientFeatures class.
Why: a fixed class silently goes stale the moment the deployed model's
feature set changes (e.g. config.yaml's production_run_id gets pointed at
a run that included comorbidity_count, or dropped a feature the old class
still required) — the API would then either reject every real request or
silently send the model garbage. Building the request model from the
ACTUAL loaded model's feature_names, looked up against
src/api/feature_specs.py for validation bounds, means the schema can never
drift out of sync with the model — there's only one source of truth
(the model's own feature_names), not two things that have to be kept
manually in sync.

RiskPrediction and ModelInfo stay static — their shape doesn't depend on
which features the model uses internally.
"""

from typing import Literal, Type

from pydantic import BaseModel, ConfigDict, Field, create_model

from src.api.feature_specs import get_spec


def build_patient_features_model(feature_names: list[str]) -> Type[BaseModel]:
    """
    Dynamically builds a Pydantic model whose fields are exactly the
    given feature names, each typed and bounded per feature_specs.py.
    """
    fields = {}
    for name in feature_names:
        py_type, ge, le, description = get_spec(name)
        field_kwargs = {"description": description}
        if ge is not None:
            field_kwargs["ge"] = ge
        if le is not None:
            field_kwargs["le"] = le
        fields[name] = (py_type, Field(..., **field_kwargs))

    return create_model("PatientFeatures", **fields)


class RiskPrediction(BaseModel):
    risk_score: float = Field(..., description="Raw Cox model output (log partial hazard) — relative ranking only, NOT a probability")
    risk_percentile: float = Field(..., description="Where this score falls relative to the training population, 0-100")
    risk_category: str = Field(..., description="'high' (top 20%), 'medium' (50-80th pct), or 'low' (bottom 50%)")
    model_run_id: str
    disclaimer: str


class ModelInfo(BaseModel):
    model_run_id: str
    model_type: str
    features: list[str]
    training_events: int
    fairness_status: str
    disclaimer: str

class QueueItem(BaseModel):
    patient_ref: str
    patient_name: str
    discharge_ts: str
    admission_reason: str
    risk_score: float
    risk_percentile: float
    risk_category: str


class PatientSearchRequest(BaseModel):
    search: str = Field(default="", max_length=200)
    category: Literal["high", "medium", "low"] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class PatientSearchResponse(BaseModel):
    items: list[QueueItem]
    total: int


class PatientHistoryItem(BaseModel):
    discharge_ts: str
    section_name: str
    text: str


class PatientHistoryResponse(BaseModel):
    items: list[PatientHistoryItem]
    offset: int
    limit: int
    has_more: bool


class FullAssessment(BaseModel):
    patient_id: str
    patient_ref: str | None = None
    discharge_ts: str
    patient_name: str
    risk_score: float
    risk_percentile: float
    risk_category: str
    admission_reason: str
    patient_context_summary: str
    categories_with_no_match: list[str]
    draft_plan: str
    critique_notes: str
    disclaimer: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    patient_name: str | None = None


class ChatToolCall(BaseModel):
    name: str
    arguments: dict
    result: str


class ChatResponse(BaseModel):
    answer: str
    tool_calls: list[ChatToolCall]


class DecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    draft_plan: str | None = Field(default=None, min_length=1)


class DecisionRecord(BaseModel):
    patient_id: str
    patient_ref: str | None = None
    discharge_ts: str
    decision: str
    decided_at: str
    actor: str
    draft_plan: str


class TransitionReport(BaseModel):
    status: str
    message: str
    report_markdown: str | None = None
    report_path: str | None = None


class FollowUpRequest(BaseModel):
    status: Literal["pending", "scheduled", "contacted", "completed", "missed", "readmitted"]
    note: str | None = Field(default=None, max_length=500)


class FollowUpRecord(BaseModel):
    patient_id: str
    patient_ref: str | None = None
    discharge_ts: str
    status: str
    actor: str
    timestamp: str
    note: str = ""


class ReminderRequest(BaseModel):
    remind_at: str = Field(..., min_length=1, max_length=40)
    message: str | None = Field(default=None, max_length=500)


class ReminderRecord(BaseModel):
    patient_id: str
    patient_ref: str | None = None
    discharge_ts: str
    remind_at: str
    message: str
    status: str
    actor: str
    timestamp: str


class DriftReport(BaseModel):
    status: str
    n_recent_predictions: int
    feature_drift: dict
    prediction_drift: dict | None
    disclaimer: str


class PrecomputeRequest(BaseModel):
    category: str | None = Field(default="high", description="Filter by risk category ('high', 'medium', or None for all)")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum number of patient plans to precompute")
    force: bool = Field(default=False, description="Re-generate even if already cached/persisted")


class PrecomputeStatus(BaseModel):
    status: str
    total: int
    completed: int
    failed: int
    skipped: int
    in_progress_patient_id: str | None = None
    current_index: int
    started_at: str | None = None
    last_completed_at: str | None = None
    errors: list[dict]


class HL7IntakeRequest(BaseModel):
    raw_message: str = Field(..., min_length=5, description="Raw pipe-delimited HL7v2 message (ADT^A03)")


class DischargeEventTriggerRequest(BaseModel):
    patient_id: str = Field(..., description="Patient ID / MRN")
    discharge_ts: str | None = Field(default=None, description="Discharge timestamp (ISO or HL7 format)")
    admit_ts: str | None = Field(default=None, description="Admit timestamp")
    encounter_id: str | None = Field(default=None, description="Encounter ID")
    patient_name: str | None = Field(default=None, description="Patient name")
    admission_reason: str | None = Field(default=None, description="Primary diagnosis or admission reason")
    fhir_bundle: dict | None = Field(default=None, description="Optional embedded FHIR bundle with clinical resources")


class IntakeResultResponse(BaseModel):
    status: str
    patient_id: str
    encounter_id: str
    discharge_ts: str
    patient_name: str
    admission_reason: str
    features: dict
    risk_score: float
    risk_percentile: float
    risk_category: str
    precompute_triggered: bool
    published_to_kafka: bool
