"""
Model Serving API — wraps the chosen Cox baseline in a REST interface.

ADAPTIVE BY DESIGN:
The /predict request schema is built at startup from whatever model
config.yaml's production_run_id actually points to (see schemas.py:
build_patient_features_model) — not hardcoded. Swap the run_id to a model
trained on a different feature set, and the API's accepted request shape,
validation bounds, and generated docs all update automatically on next
restart. There's exactly one source of truth (the model's own
feature_names), not a model file and a separately-maintained schema that
can silently drift apart.

DRIFT MONITORING:
Every served prediction is logged (inputs + output) to
results/prediction_log.csv. /drift-report compares the recent log against
the training reference distribution using a Kolmogorov-Smirnov test, for
both individual features (DATA drift) and the model's own output
distribution (PREDICTION drift) — gated on a minimum sample size so it
doesn't report a false signal from a handful of early requests.

WEB APP ENDPOINTS (added for the Clinician Web App layer):
/patients — the risk queue, pre-scored once at startup, not re-computed
per request. /patients/{id}/assessment — runs the full orchestrator
(real LLM calls). /chat — wraps the tool-calling chat agent (real LLM
calls). CORS enabled for the Vite dev server.

Also deliberately honest about two things easy to misrepresent by
accident: Cox's predict() output is a risk SCORE, not a probability
(only meaningful in relative terms); and this model's fairness audit is
INCONCLUSIVE, not passed (see results/RESULTS.md) — every response
carries that disclaimer.
"""

import sys

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()  # must run before /assessment or /chat are ever called —
                # these now invoke Groq directly from within the API
                # process, which previously only happened from each
                # agent script's own __main__ block

sys.path.insert(0, ".")
from src.api.drift_monitor import compute_drift_report, log_prediction
from src.api.schemas import (
    ChatRequest,
    ChatResponse,
    DriftReport,
    FullAssessment,
    ModelInfo,
    QueueItem,
    RiskPrediction,
    build_patient_features_model,
)
from src.model.experiment_registry import load_model
from src.utils.config import load_config
from src.utils.logging_config import setup_logging

setup_logging()

DISCLAIMER = (
    "Research/portfolio baseline model. Trained on ~52 positive events — "
    "treat as directional, not precise. Fairness audit INCONCLUSIVE for "
    "sex and race at current dataset size (see results/RESULTS.md). "
    "Not validated for clinical use."
)

app = FastAPI(
    title="Care Transition Copilot — Risk Model API",
    description="Serves 30-day readmission risk scores from the currently-configured model run.",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

_state = {}


def _categorize(percentile: float) -> str:
    if percentile >= 80:
        return "high"
    if percentile >= 50:
        return "medium"
    return "low"


@app.on_event("startup")
def load_production_model():
    cfg = load_config()
    if not cfg.production_run_id:
        raise RuntimeError(
            "config.yaml has no model.production_run_id set. Check "
            "results/experiments.csv for the run you want to serve, then add:\n"
            "  model:\n    production_run_id: \"<run_id>\"\nto config.yaml."
        )

    model, feature_names = load_model(cfg.production_run_id)

    df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))
    modeling_df = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])].copy()
    X_ref = modeling_df[feature_names].copy()
    for col in feature_names:
        if X_ref[col].dtype == bool:
            X_ref[col] = X_ref[col].astype(int)
    reference_scores = model.predict(X_ref)

    _state["model"] = model
    _state["feature_names"] = feature_names
    _state["reference_df"] = X_ref
    _state["reference_scores"] = reference_scores
    _state["run_id"] = cfg.production_run_id
    _state["training_events"] = int(modeling_df["event_observed"].sum())
    _state["queue_df"] = modeling_df[["patient_id", "patient_name", "discharge_ts", "admission_reason"]].reset_index(drop=True)

    PatientFeaturesModel = build_patient_features_model(feature_names)
    _state["patient_schema"] = PatientFeaturesModel

    def predict(patient: PatientFeaturesModel) -> RiskPrediction:
        payload = patient.model_dump()
        row = pd.DataFrame([{col: (int(payload[col]) if isinstance(payload[col], bool) else payload[col])
                              for col in feature_names}])[feature_names]

        risk_score = float(model.predict(row)[0])
        percentile = float((reference_scores < risk_score).mean() * 100)
        category = _categorize(percentile)

        log_prediction(feature_values=payload, risk_score=risk_score)

        return RiskPrediction(
            risk_score=round(risk_score, 4),
            risk_percentile=round(percentile, 1),
            risk_category=category,
            model_run_id=cfg.production_run_id,
            disclaimer=DISCLAIMER,
        )

    app.add_api_route("/predict", predict, methods=["POST"], response_model=RiskPrediction)


@app.get("/health")
def health():
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"status": "ok", "model_run_id": _state["run_id"]}


@app.get("/model-info", response_model=ModelInfo)
def model_info():
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return ModelInfo(
        model_run_id=_state["run_id"],
        model_type=type(_state["model"]).__name__,
        features=_state["feature_names"],
        training_events=_state["training_events"],
        fairness_status="INCONCLUSIVE — see results/RESULTS.md",
        disclaimer=DISCLAIMER,
    )


@app.get("/drift-report", response_model=DriftReport)
def drift_report():
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    report = compute_drift_report(
        reference_df=_state["reference_df"],
        reference_scores=_state["reference_scores"],
        feature_names=_state["feature_names"],
    )
    return DriftReport(**report, disclaimer=DISCLAIMER)


@app.get("/patients", response_model=list[QueueItem])
def patient_queue(limit: int = 50, category: str | None = None):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")

    queue_df = _state["queue_df"]
    scores = _state["reference_scores"]

    items = []
    for i in range(len(queue_df)):
        risk_score = float(scores[i])
        percentile = float((scores < risk_score).mean() * 100)
        cat = _categorize(percentile)
        items.append(QueueItem(
            patient_id=queue_df.iloc[i]["patient_id"],
            patient_name=queue_df.iloc[i]["patient_name"],
            discharge_ts=str(queue_df.iloc[i]["discharge_ts"]),
            admission_reason=str(queue_df.iloc[i]["admission_reason"]),
            risk_score=round(risk_score, 4),
            risk_percentile=round(percentile, 1),
            risk_category=cat,
        ))

    if category:
        items = [i for i in items if i.risk_category == category]
    items.sort(key=lambda i: i.discharge_ts, reverse=True)
    return items[:limit]


@app.get("/patients/{patient_id}/assessment", response_model=FullAssessment)
def patient_assessment(patient_id: str):
    """
    Runs the full orchestrator graph (risk -> retrieval -> reasoning ->
    critique) for one patient. Makes real LLM calls — slower and costs
    real API usage, unlike /patients which is pre-computed.
    """
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")

    from src.agents.orchestrator import LOW_RISK_CATEGORY, build_graph

    try:
        graph = build_graph()
        result = graph.invoke({"patient_id": patient_id})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    is_low_risk = result["risk_category"] == LOW_RISK_CATEGORY
    return FullAssessment(
        patient_id=patient_id,
        risk_score=result["risk_score"],
        risk_percentile=result["risk_percentile"],
        risk_category=result["risk_category"],
        admission_reason=result["admission_reason"],
        patient_context_summary=result.get("patient_context_summary", ""),
        categories_with_no_match=result.get("categories_with_no_match", []),
        draft_plan=result["final_summary"] if is_low_risk else result["draft_plan"],
        critique_notes=result.get("critique_notes", ""),
        disclaimer=DISCLAIMER,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Wraps the tool-calling chat agent (src/agents/chat_agent.py)."""
    from src.agents.chat_agent import ask

    try:
        result = ask(request.question)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return ChatResponse(
        answer=result["answer"],
        tool_calls=[
            {"name": tc["name"], "arguments": tc["arguments"], "result": tc["result"]}
            for tc in result["tool_calls"]
        ],
    )