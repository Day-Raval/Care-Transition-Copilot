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

from typing import Type

from pydantic import BaseModel, Field, create_model

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
    patient_id: str
    patient_name: str
    discharge_ts: str
    admission_reason: str
    risk_score: float
    risk_percentile: float
    risk_category: str


class FullAssessment(BaseModel):
    patient_id: str
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
    question: str


class ChatToolCall(BaseModel):
    name: str
    arguments: dict
    result: str


class ChatResponse(BaseModel):
    answer: str
    tool_calls: list[ChatToolCall]


class DecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    actor: str = Field("demo_clinician", min_length=1, max_length=120)


class DecisionRecord(BaseModel):
    patient_id: str
    discharge_ts: str
    decision: str
    decided_at: str
    actor: str
    draft_plan: str


class DriftReport(BaseModel):
    status: str
    n_recent_predictions: int
    feature_drift: dict
    prediction_drift: dict | None
    disclaimer: str
