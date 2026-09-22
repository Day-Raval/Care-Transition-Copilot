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

import logging
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()  # must run before /assessment or /chat are ever called —
                # these now invoke Groq directly from within the API
                # process, which previously only happened from each
                # agent script's own __main__ block

sys.path.insert(0, ".")
from src.api.drift_monitor import compute_drift_report, log_prediction
from src.api.production import CARE_PLANS_PATH, log_audit_event, read_jsonl, runtime_dependency_report, save_care_plan
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
logger = logging.getLogger(__name__)

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


@app.middleware("http")
async def optional_demo_auth(request: Request, call_next):
    expected_token = os.getenv("DEMO_API_KEY")
    if expected_token and request.url.path != "/health":
        provided_token = request.headers.get("x-demo-token")
        if provided_token != expected_token:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid demo token"})
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled API error path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Unexpected server error. Check the API logs for details."},
    )


def _categorize(percentile: float) -> str:
    if percentile >= 80:
        return "high"
    if percentile >= 50:
        return "medium"
    return "low"


@app.on_event("startup")
def load_production_model():
    cfg = load_config()
    dependencies = runtime_dependency_report(cfg)
    _state["runtime_dependencies"] = dependencies
    if not cfg.production_run_id:
        raise RuntimeError(
            "config.yaml has no model.production_run_id set. Check "
            "results/experiments.csv for the run you want to serve, then add:\n"
            "  model:\n    production_run_id: \"<run_id>\"\nto config.yaml."
        )
    if not dependencies["checks"]["processed_dataset"]:
        raise RuntimeError(
            "Processed modeling dataset is missing. Expected "
            f"{cfg.output_csv.replace('.csv', '_with_target.csv')}. Run the data pipeline first."
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
    dependencies = _state.get("runtime_dependencies", {"ready": True, "missing": [], "checks": {}})
    return {
        "status": "ok" if dependencies["ready"] else "degraded",
        "model_run_id": _state["run_id"],
        "dependencies": dependencies,
    }


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
    assessment = FullAssessment(
        patient_id=patient_id,
        patient_name=result["patient_name"],
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
    save_care_plan(
        {
            "patient_id": assessment.patient_id,
            "patient_name": assessment.patient_name,
            "risk_category": assessment.risk_category,
            "risk_percentile": assessment.risk_percentile,
            "admission_reason": assessment.admission_reason,
            "draft_plan": assessment.draft_plan,
            "critique_notes": assessment.critique_notes,
            "model_run_id": _state["run_id"],
        }
    )
    log_audit_event(
        "assessment_generated",
        patient_id=assessment.patient_id,
        risk_category=assessment.risk_category,
        risk_percentile=assessment.risk_percentile,
    )
    return assessment


@app.get("/care-plans")
def saved_care_plans(limit: int = 50):
    return read_jsonl(CARE_PLANS_PATH, limit=limit)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Wraps the tool-calling chat agent (src/agents/chat_agent.py)."""
    from src.agents.chat_agent import ask

    try:
        result = ask(request.question)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    log_audit_event(
        "chat_completed",
        question=request.question,
        tool_calls=result["tool_calls"],
        answer=result["answer"],
    )

    return ChatResponse(
        answer=result["answer"],
        tool_calls=[
            {"name": tc["name"], "arguments": tc["arguments"], "result": tc["result"]}
            for tc in result["tool_calls"]
        ],
    )
