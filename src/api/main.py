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
results/operations/prediction_log.csv. /drift-report compares the recent log against
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
INCONCLUSIVE, not passed (see results/modeling/RESULTS.md) — every response
carries that disclaimer.
"""

from __future__ import annotations

import logging
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any

import jwt
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
from src.api.production import (
    DEFAULT_ACTOR,
    build_transition_report,
    init_decision_db,
    latest_decision,
    latest_follow_up,
    latest_saved_care_plan,
    load_discharge_records,
    log_audit_event,
    notify_care_plan_decision,
    read_audit_events,
    read_care_plans,
    read_decisions,
    read_fhir_writebacks,
    read_follow_ups,
    read_notifications,
    read_reminders,
    record_fhir_writeback,
    reset_audit_request_id,
    runtime_dependency_report,
    save_care_plan,
    save_decision,
    save_follow_up_status,
    save_transition_report,
    schedule_notification_reminder,
    set_audit_request_id,
)
from src.api.precompute import precompute_manager
from src.api.security import OIDCConfigurationError, required_roles, verify_oidc_access_token
from src.api.schemas import (
    ChatRequest,
    ChatResponse,
    DecisionRecord,
    DecisionRequest,
    DischargeEventTriggerRequest,
    DriftReport,
    FullAssessment,
    FollowUpRecord,
    FollowUpRequest,
    HL7IntakeRequest,
    IntakeResultResponse,
    ModelInfo,
    PatientHistoryItem,
    PatientHistoryResponse,
    PatientSearchRequest,
    PatientSearchResponse,
    PrecomputeRequest,
    PrecomputeStatus,
    QueueItem,
    ReminderRecord,
    ReminderRequest,
    RiskPrediction,
    TransitionReport,
    build_patient_features_model,
)
from src.data_services.consumer import KafkaEventConsumer, get_kafka_consumer
from src.ingestion.hl7_intake import process_realtime_intake
from src.model.experiment_registry import load_model
from src.utils.config import load_config
from src.utils.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Research/portfolio baseline model. Trained on ~52 positive events — "
    "treat as directional, not precise. Fairness audit INCONCLUSIVE for "
    "sex and race at current dataset size (see results/modeling/RESULTS.md). "
    "Not validated for clinical use."
)
MASKED_PATIENT_ID = "[masked patient ID]"
PATIENT_ID_GUARDRAIL = "Patient IDs are not accepted in chat. Search by patient name instead."

app = FastAPI(
    title="Care Transition Copilot — Risk Model API",
    description="Serves 30-day readmission risk scores from the currently-configured model run.",
    version="0.3.0",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID", "X-Clinician-ID"],
)

_state = {}
_redis_cache = None


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    audit_token = set_audit_request_id(request_id)
    request.state.request_id = request_id
    try:
        path = request.url.path
        method = request.method
        if path == "/health" or method == "OPTIONS":
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        auth_mode = os.getenv("AUTH_MODE", "api_key").strip().lower()
        expected_key = os.getenv("API_KEY")
        provided_key = request.headers.get("x-api-key")
        if auth_mode == "oidc" and path == "/predict":
            if not expected_key or provided_key != expected_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid service API key", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )
            request.state.principal = {"sub": "risk_model_service", "roles": [], "auth_mode": "service"}
        elif auth_mode == "oidc":
            authorization = request.headers.get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "A bearer access token is required", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )
            try:
                request.state.principal = verify_oidc_access_token(token)
            except OIDCConfigurationError as exc:
                logger.error("OIDC authentication is misconfigured: %s", exc)
                return JSONResponse(
                    status_code=503,
                    content={"detail": "OIDC authentication is not configured", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )
            except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired bearer access token", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )
            allowed_roles = required_roles(method, path)
            if allowed_roles is not None and not allowed_roles.intersection(request.state.principal["roles"]):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Your role is not authorized for this action", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )
        elif auth_mode == "api_key":
            if not expected_key or provided_key != expected_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid API key", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )
            request.state.principal = {
                "sub": request.headers.get("x-clinician-id", DEFAULT_ACTOR),
                "roles": ["admin"],
                "auth_mode": "api_key",
            }
        else:
            return JSONResponse(
                status_code=503,
                content={"detail": "AUTH_MODE must be 'api_key' or 'oidc'", "request_id": request_id},
                headers={"X-Request-ID": request_id},
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        reset_audit_request_id(audit_token)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    route_path = getattr(request.scope.get("route"), "path", request.url.path)
    logger.exception("Unhandled API error path=%s request_id=%s", route_path, request_id)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Unexpected server error. Check the API logs for details.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def _categorize(percentile: float) -> str:
    if percentile >= 80:
        return "high"
    if percentile >= 50:
        return "medium"
    return "low"


def _cache_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("CACHE_TTL_SECONDS", "600")))
    except ValueError:
        return 600


def _assessment_cache_key(patient_id: str, discharge_ts: str) -> str:
    identity = "\0".join(
        (_state.get("run_id", ""), patient_id, discharge_ts)
    ).encode("utf-8")
    return f"care-transition:assessment:v1:{hashlib.sha256(identity).hexdigest()}"


def _assessment_has_required_context(payload: dict[str, Any]) -> bool:
    return payload.get("risk_category") == "low" or bool(payload.get("patient_context_summary"))


def _redis_cache_client():
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return None
    global _redis_cache
    if _redis_cache is None:
        try:
            import redis
        except ImportError:
            logger.warning("REDIS_URL is set but the redis package is not installed; using in-memory cache")
            return None
        _redis_cache = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _redis_cache


def _clinician_actor(request: Request) -> str:
    principal = getattr(request.state, "principal", {})
    if principal.get("auth_mode") == "oidc":
        return principal["sub"]
    actor = request.headers.get("x-clinician-id") or DEFAULT_ACTOR
    return actor.strip() or DEFAULT_ACTOR


def _latest_discharge_ts(patient_id: str, discharge_ts: str | None = None) -> str:
    queue_df = _state["queue_df"]
    patient_rows = queue_df[queue_df["patient_id"] == patient_id]
    if patient_rows.empty:
        raise HTTPException(status_code=404, detail="No episode found for patient reference")
    if discharge_ts is not None:
        episode_rows = patient_rows[patient_rows["discharge_ts"].astype(str) == discharge_ts]
        if episode_rows.empty:
            raise HTTPException(status_code=404, detail="No episode found for patient reference and discharge timestamp")
        return str(episode_rows.sort_values("discharge_ts").iloc[-1]["discharge_ts"])
    return str(patient_rows.sort_values("discharge_ts").iloc[-1]["discharge_ts"])


def _patient_ref(patient_id: str) -> str:
    salt = os.getenv("PATIENT_REF_SALT") or os.getenv("API_KEY") or _state.get("run_id", "")
    return hashlib.sha256(f"{salt}\0{patient_id}".encode("utf-8")).hexdigest()[:16]


def _resolve_patient_key(patient_key: str) -> str:
    if "queue_df" not in _state:
        return patient_key
    queue_df = _state["queue_df"]
    patient_ids = [str(pid) for pid in queue_df["patient_id"].drop_duplicates()]
    if patient_key in patient_ids:
        return patient_key
    for patient_id in patient_ids:
        if _patient_ref(patient_id) == patient_key:
            return patient_id
    raise HTTPException(status_code=404, detail="No episode found for patient reference")


def _redact_patient_record(record: dict[str, Any]) -> dict[str, Any]:
    public = json.loads(json.dumps(record, ensure_ascii=True, default=str))

    def redact(value):
        if isinstance(value, dict):
            next_value = {}
            for key, item in value.items():
                if key == "patient_id":
                    next_value["patient_ref"] = _patient_ref(str(item))
                else:
                    next_value[key] = redact(item)
            return next_value
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str) and "queue_df" in _state:
            for patient_id in _state["queue_df"]["patient_id"].drop_duplicates().astype(str):
                value = value.replace(patient_id, _patient_ref(patient_id))
        return value

    return redact(public)


def _replace_patient_refs_with_ids(text: str) -> str:
    if not _state or "queue_df" not in _state:
        return text
    for patient_id in _state["queue_df"]["patient_id"].drop_duplicates().astype(str):
        text = text.replace(_patient_ref(patient_id), patient_id)
    return text


def _mask_patient_ids(text: str) -> str:
    text = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        MASKED_PATIENT_ID,
        text,
    )
    if _state and "queue_df" in _state:
        for patient_id in _state["queue_df"]["patient_id"].drop_duplicates().astype(str):
            text = text.replace(patient_id, MASKED_PATIENT_ID).replace(_patient_ref(patient_id), MASKED_PATIENT_ID)
    return text


def _contains_patient_identifier(text: str) -> bool:
    if re.search(r"\b(patient[_ -]?(id|ref)|mrn|medical record number)\b", text, re.I):
        return True
    if re.search(r"\b[0-9a-fA-F]{16}\b", text):
        return True
    if re.search(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", text):
        return True
    if _state and "queue_df" in _state:
        for patient_id in _state["queue_df"]["patient_id"].drop_duplicates().astype(str):
            if patient_id in text or _patient_ref(patient_id) in text:
                return True
    return False


def _normalize_patient_name(value: str) -> str:
    without_digits = re.sub(r"\d+", "", str(value).lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", without_digits)).strip()


def _resolve_patient_name(name: str) -> dict[str, str] | None:
    if not _state or "queue_df" not in _state:
        return None
    requested = _normalize_patient_name(name)
    if not requested:
        return None

    rows = list(_state["queue_df"].itertuples())
    matches = [row for row in rows if _normalize_patient_name(row.patient_name) == requested]
    if len(matches) != 1:
        matches = [row for row in rows if requested in _normalize_patient_name(row.patient_name)]

    patient_ids = {str(row.patient_id) for row in matches}
    if len(patient_ids) != 1:
        return None
    row = matches[0]
    return {"patient_id": str(row.patient_id), "patient_name": str(row.patient_name)}


def _medication_items_from_context(results: list[dict[str, Any]]) -> list[str]:
    items = []
    seen = set()
    for result in results:
        text = re.sub(r"Allergies:\s*No Known Allergies\.?\.?\s*", "", str(result.get("text", "")), flags=re.I)
        text = re.sub(r"^.*?medications?:\s*", "", text, flags=re.I | re.S)
        text = re.sub(r"^The patient was prescribed the following medications:\s*", "", text, flags=re.I)
        for part in re.split(r"\n|;", text):
            item = re.sub(r"^[-*\s]+", "", part).strip(" .")
            if not item or "allerg" in item.lower():
                continue
            key = re.sub(r"[^a-z0-9]+", " ", item.lower()).strip()
            if key and key not in seen:
                seen.add(key)
                items.append(item)
    return items


def _chat_fast_path(question: str, resolved_patient: dict[str, str] | None) -> dict[str, Any] | None:
    if not resolved_patient:
        return None
    normalized = re.sub(r"[^a-z ]", " ", question.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in {"review readmission risk", "readmission risk", "risk"}:
        from src.agents.risk_tool import assess_risk
        from src.agents.tools import RISK_INTERPRETATION

        result = assess_risk(resolved_patient["patient_id"])
        category = str(result["risk_category"]).lower()
        interpretation = RISK_INTERPRETATION.get(
            category,
            "Review the care plan and discharge context to decide follow-up urgency.",
        )
        tool_result = (
            f"Readmission risk: {category}. "
            f"Plain-language interpretation: {interpretation} "
            f"admission reason: {result['admission_reason']}"
        )
        return {
            "answer": f"This patient is {category} risk. {interpretation} Admission reason: {result['admission_reason']}.",
            "tool_calls": [
                {
                    "name": "assess_readmission_risk",
                    "arguments": {"patient_id": resolved_patient["patient_id"]},
                    "result": tool_result,
                }
            ],
        }
    if re.search(r"\b(meds?|medications?|drugs?|prescriptions?)\b", normalized):
        from src.retrieval.query_store import get_collection, retrieve_relevant_context

        query = "current medications at discharge"
        results = retrieve_relevant_context(get_collection(), resolved_patient["patient_id"], query)
        if not results:
            answer = "I did not find medication-specific chart documentation for this patient."
            tool_result = "No relevant medication documentation found."
        else:
            meds = _medication_items_from_context(results)
            if meds:
                answer = "Yes. Documented medications include:\n" + "\n".join(f"- {med}" for med in meds)
                tool_result = "\n".join(f"[{r['section']}] {r['text']}" for r in results)
            else:
                answer = "Medication-related chart text was found, but I could not extract a clean medication list from it."
                tool_result = "\n".join(f"[{r['section']}] {r['text']}" for r in results)
        return {
            "answer": answer,
            "tool_calls": [
                {
                    "name": "search_patient_chart",
                    "arguments": {"patient_id": resolved_patient["patient_id"], "query": query},
                    "result": tool_result,
                }
            ],
        }
    return None


def _redact_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    redacted["answer"] = _mask_patient_ids(str(redacted.get("answer", "")))
    if not _state or "queue_df" not in _state:
        return redacted
    for patient_id in _state["queue_df"]["patient_id"].drop_duplicates().astype(str):
        ref = _patient_ref(patient_id)
        for call in redacted["tool_calls"]:
            if call.get("arguments", {}).get("patient_id") == patient_id:
                call["arguments"].pop("patient_id", None)
                call["arguments"]["patient_ref"] = ref
            call["result"] = call["result"].replace(f"patient_id: {patient_id}", f"patient_ref: {ref}")
            call["result"] = call["result"].replace(patient_id, ref)
    return redacted


def _assessment_cache_get(patient_id: str, discharge_ts: str) -> FullAssessment | None:
    ttl = _cache_ttl_seconds()
    key = (patient_id, discharge_ts)
    if ttl > 0:
        redis_client = _redis_cache_client()
        if redis_client is not None:
            try:
                cached_json = redis_client.get(_assessment_cache_key(*key))
                if cached_json:
                    payload = json.loads(cached_json)
                    payload.setdefault("patient_ref", _patient_ref(patient_id))
                    if not _assessment_has_required_context(payload):
                        return None
                    with _state["assessment_cache_lock"]:
                        _state["assessment_cache"][key] = (
                            time.monotonic() + ttl,
                            payload,
                        )
                    return FullAssessment(**payload)
            except Exception as exc:
                logger.warning("Redis assessment cache read failed; using in-memory cache: %s", exc)
        with _state["assessment_cache_lock"]:
            cached = _state["assessment_cache"].get(key)
            if cached:
                expires_at, payload = cached
                if expires_at >= time.monotonic():
                    payload.setdefault("patient_ref", _patient_ref(patient_id))
                    if not _assessment_has_required_context(payload):
                        _state["assessment_cache"].pop(key, None)
                        return None
                    return FullAssessment(**payload)
                else:
                    _state["assessment_cache"].pop(key, None)

    # Check persistence backend (saved care plans)
    saved = latest_saved_care_plan(patient_id, discharge_ts)
    if saved and "draft_plan" in saved and "patient_name" in saved:
        risk_category = saved.get("risk_category", "medium")
        if not _assessment_has_required_context({"risk_category": risk_category, **saved}):
            return None
        payload = {
            "patient_id": saved["patient_id"],
            "patient_ref": _patient_ref(str(saved["patient_id"])),
            "discharge_ts": str(saved.get("discharge_ts", discharge_ts)),
            "patient_name": saved["patient_name"],
            "risk_score": float(saved.get("risk_score", 0.0)),
            "risk_percentile": float(saved.get("risk_percentile", 0.0)),
            "risk_category": risk_category,
            "admission_reason": saved.get("admission_reason", ""),
            "patient_context_summary": saved.get("patient_context_summary", ""),
            "categories_with_no_match": saved.get("categories_with_no_match", []),
            "draft_plan": saved["draft_plan"],
            "critique_notes": saved.get("critique_notes", ""),
            "disclaimer": DISCLAIMER,
        }
        assessment = FullAssessment(**payload)
        _assessment_cache_set(assessment)
        return assessment

    return None


def _assessment_cache_set(assessment: FullAssessment) -> None:
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        return
    key = (assessment.patient_id, assessment.discharge_ts)
    payload = assessment.model_dump()
    with _state["assessment_cache_lock"]:
        _state["assessment_cache"][key] = (
            time.monotonic() + ttl,
            payload,
        )
    redis_client = _redis_cache_client()
    if redis_client is not None:
        try:
            redis_client.set(
                _assessment_cache_key(*key),
                json.dumps(payload, ensure_ascii=True, default=str),
                ex=ttl,
            )
        except Exception as exc:
            logger.warning("Redis assessment cache write failed; using in-memory cache: %s", exc)


def _generate_assessment(patient_id: str, discharge_ts: str) -> FullAssessment:
    from src.agents.orchestrator import LOW_RISK_CATEGORY, build_graph

    try:
        graph = build_graph()
        result = graph.invoke({"patient_id": patient_id, "discharge_ts": discharge_ts})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    is_low_risk = result["risk_category"] == LOW_RISK_CATEGORY
    assessment = FullAssessment(
        patient_id=patient_id,
        patient_ref=_patient_ref(patient_id),
        discharge_ts=discharge_ts,
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
            "discharge_ts": assessment.discharge_ts,
            "patient_name": assessment.patient_name,
            "risk_score": assessment.risk_score,
            "risk_category": assessment.risk_category,
            "risk_percentile": assessment.risk_percentile,
            "admission_reason": assessment.admission_reason,
            "patient_context_summary": assessment.patient_context_summary,
            "categories_with_no_match": assessment.categories_with_no_match,
            "draft_plan": assessment.draft_plan,
            "critique_notes": assessment.critique_notes,
            "model_run_id": _state["run_id"],
        }
    )
    log_audit_event(
        "assessment_generated",
        patient_id=assessment.patient_id,
        discharge_ts=assessment.discharge_ts,
        risk_category=assessment.risk_category,
        risk_percentile=assessment.risk_percentile,
    )
    _assessment_cache_set(assessment)
    return assessment


def _queue_candidates() -> list[dict[str, Any]]:
    if not _state or "queue_df" not in _state:
        return []
    queue_df = _state["queue_df"]
    scores = _state["reference_scores"]
    candidates = []
    for i in range(len(queue_df)):
        risk_score = float(scores[i])
        percentile = float((scores < risk_score).mean() * 100)
        cat = _categorize(percentile)
        candidates.append({
            "patient_id": str(queue_df.iloc[i]["patient_id"]),
            "patient_name": str(queue_df.iloc[i]["patient_name"]),
            "discharge_ts": str(queue_df.iloc[i]["discharge_ts"]),
            "admission_reason": str(queue_df.iloc[i]["admission_reason"]),
            "risk_score": round(risk_score, 4),
            "risk_percentile": round(percentile, 1),
            "risk_category": cat,
        })
    candidates.sort(key=lambda x: x["risk_score"], reverse=True)
    return candidates


def _is_assessment_ready(patient_id: str, discharge_ts: str) -> bool:
    return _assessment_cache_get(patient_id, discharge_ts) is not None



@app.on_event("startup")
def load_production_model():
    cfg = load_config()
    _state["config"] = cfg
    dependencies = runtime_dependency_report(cfg)
    _state["runtime_dependencies"] = dependencies
    if not cfg.production_run_id:
        raise RuntimeError(
            "config.yaml has no model.production_run_id set. Check "
            "results/modeling/experiments.csv for the run you want to serve, then add:\n"
            "  model:\n    production_run_id: \"<run_id>\"\nto config.yaml."
        )
    if not dependencies["checks"]["processed_dataset"]:
        raise RuntimeError(
            "Processed modeling dataset is missing. Expected "
            f"{cfg.output_csv.replace('.csv', '_with_target.csv')}. Run the data pipeline first."
        )

    model, feature_names = load_model(cfg.production_run_id)

    df = load_discharge_records(cfg)
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
    _state["assessment_cache"] = {}
    _state["assessment_cache_lock"] = threading.Lock()
    init_decision_db()

    PatientFeaturesModel = build_patient_features_model(feature_names)
    _state["patient_schema"] = PatientFeaturesModel

    def predict(patient: PatientFeaturesModel) -> RiskPrediction:
        payload = patient.model_dump()
        row = pd.DataFrame([{col: (int(payload[col]) if isinstance(payload[col], bool) else payload[col])
                              for col in feature_names}])[feature_names]

        risk_score = float(model.predict(row)[0])
        percentile = float((reference_scores < risk_score).mean() * 100)
        category = _categorize(percentile)

        log_prediction(feature_values=payload, risk_score=risk_score, model_run_id=cfg.production_run_id)

        return RiskPrediction(
            risk_score=round(risk_score, 4),
            risk_percentile=round(percentile, 1),
            risk_category=category,
            model_run_id=cfg.production_run_id,
            disclaimer=DISCLAIMER,
        )

    app.add_api_route("/predict", predict, methods=["POST"], response_model=RiskPrediction)

    if os.getenv("PRECOMPUTE_ON_STARTUP", "false").lower() == "true":
        logger.info("PRECOMPUTE_ON_STARTUP is enabled; launching background care plan precomputation")
        precompute_manager.start_background_precompute(
            get_candidates_fn=_queue_candidates,
            generate_fn=_generate_assessment,
            is_cached_fn=_is_assessment_ready,
            categories=["high"],
            limit=int(os.getenv("PRECOMPUTE_STARTUP_LIMIT", "10")),
            force=False,
        )

    if os.getenv("KAFKA_CONSUMER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            logger.info("KAFKA_CONSUMER_ENABLED is true; starting background Kafka event consumer")
            consumer = get_kafka_consumer()
            consumer.start()
            _state["kafka_consumer"] = consumer
        except Exception as ce:
            logger.error("Failed to start background Kafka consumer: %s", ce)


@app.get("/health")
def health():
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    dependencies = runtime_dependency_report(_state.get("config", load_config()))
    _state["runtime_dependencies"] = dependencies
    consumer = _state.get("kafka_consumer")
    return {
        "status": "ok" if dependencies["ready"] else "degraded",
        "model_run_id": _state["run_id"],
        "dependencies": dependencies,
        "kafka_consumer": consumer.stats if consumer else {"status": "disabled"},
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
        fairness_status="INCONCLUSIVE — see results/modeling/RESULTS.md",
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

    items = _build_patient_queue_items(category=category)
    return items[:limit]


@app.post("/patients/search", response_model=PatientSearchResponse)
def search_patient_queue(query: PatientSearchRequest):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")

    items = _build_patient_queue_items(category=query.category, search=query.search)
    total = len(items)
    page = items[query.offset:query.offset + query.limit]
    return PatientSearchResponse(items=page, total=total)


def _build_patient_queue_items(category: str | None = None, search: str = "") -> list[QueueItem]:
    queue_df = _state["queue_df"]
    scores = _state["reference_scores"]
    percentiles = (pd.Series(scores).rank(method="min").sub(1).div(len(scores)).mul(100).to_numpy())
    care_plans = {
        (str(plan.get("patient_id")), str(plan.get("discharge_ts"))): plan
        for plan in read_care_plans(limit=10000)
    }
    decisions = {
        (str(decision.get("patient_id")), str(decision.get("discharge_ts"))): decision
        for decision in read_decisions(limit=10000)
    }

    items = []
    for i in range(len(queue_df)):
        patient_id = str(queue_df.iloc[i]["patient_id"])
        discharge_ts = str(queue_df.iloc[i]["discharge_ts"])
        risk_score = float(scores[i])
        percentile = float(percentiles[i])
        cat = _categorize(percentile)
        saved_plan = care_plans.get((patient_id, discharge_ts))
        decision = decisions.get((patient_id, discharge_ts))
        items.append(QueueItem(
            patient_ref=_patient_ref(patient_id),
            patient_name=queue_df.iloc[i]["patient_name"],
            discharge_ts=discharge_ts,
            admission_reason=str(queue_df.iloc[i]["admission_reason"]),
            risk_score=round(risk_score, 4),
            risk_percentile=round(percentile, 1),
            risk_category=cat,
            decision_status=decision["decision"] if decision else None,
            needs_review=decision is None,
            missing_evidence=bool(saved_plan and saved_plan.get("categories_with_no_match")),
            precomputed=saved_plan is not None,
        ))

    if category:
        items = [i for i in items if i.risk_category == category]
    items.sort(key=lambda i: i.discharge_ts, reverse=True)

    normalized_search = search.strip().casefold()
    if normalized_search:
        items = [
            item
            for item in items
            if normalized_search in " ".join(
                (item.patient_name, item.admission_reason, item.risk_category, item.discharge_ts)
            ).casefold()
        ]
    return items


def _validate_reminder_time(remind_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="remind_at must be an ISO date/time")
    return parsed.isoformat(timespec="minutes")


@app.get("/patients/{patient_ref}/history", response_model=PatientHistoryResponse)
def patient_history(patient_ref: str, limit: int = 50, offset: int = 0):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be non-negative")

    patient_id = _resolve_patient_key(patient_ref)
    from src.retrieval.query_store import get_collection

    collection = get_collection()
    page_records = collection.get(
        where={"patient_id": patient_id},
        include=["documents", "metadatas"],
        limit=limit + 1,
        offset=offset,
    )
    has_more = len(page_records["ids"]) > limit
    history_items = [
        PatientHistoryItem(
            discharge_ts=str(metadata.get("discharge_ts", "")),
            section_name=str(metadata.get("section_name", "Chart note")),
            text=document,
        )
        for document, metadata in zip(
            page_records["documents"][:limit], page_records["metadatas"][:limit]
        )
    ]
    return PatientHistoryResponse(
        items=history_items,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )


@app.get("/patients/{patient_ref}/assessment", response_model=FullAssessment, response_model_exclude={"patient_id"})
def patient_assessment(patient_ref: str, discharge_ts: str | None = None):
    """
    Runs the full orchestrator graph (risk -> retrieval -> reasoning ->
    critique) for one patient. If precomputed/cached, returns instantly.
    If currently being generated by a background task or concurrent request,
    waits on the in-flight event rather than triggering duplicate LLM calls.
    """
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")

    patient_id = _resolve_patient_key(patient_ref)
    episode_discharge_ts = _latest_discharge_ts(patient_id, discharge_ts)
    cached = _assessment_cache_get(patient_id, episode_discharge_ts)
    if cached is not None:
        return cached

    evt, is_creator = precompute_manager.get_patient_event(patient_id, episode_discharge_ts)
    if not is_creator:
        evt.wait(timeout=120)
        cached = _assessment_cache_get(patient_id, episode_discharge_ts)
        if cached is not None:
            return cached

    try:
        return _generate_assessment(patient_id, episode_discharge_ts)
    finally:
        if is_creator:
            precompute_manager.finish_patient_event(patient_id, episode_discharge_ts)



@app.get("/patients/{patient_ref}/decision", response_model=DecisionRecord | None, response_model_exclude={"patient_id"})
def patient_decision(patient_ref: str, discharge_ts: str | None = None):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    patient_id = _resolve_patient_key(patient_ref)
    episode_discharge_ts = _latest_discharge_ts(patient_id, discharge_ts) if discharge_ts else None
    record = latest_decision(patient_id, episode_discharge_ts)
    return {**record, "patient_ref": _patient_ref(patient_id)} if record else None


@app.post("/patients/{patient_ref}/decision", response_model=DecisionRecord, response_model_exclude={"patient_id"})
def decide_patient_plan(
    patient_ref: str,
    decision_request: DecisionRequest,
    http_request: Request,
    discharge_ts: str | None = None,
):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    patient_id = _resolve_patient_key(patient_ref)
    episode_discharge_ts = _latest_discharge_ts(patient_id, discharge_ts)
    assessment = _assessment_cache_get(patient_id, episode_discharge_ts)
    if assessment is None:
        assessment = _generate_assessment(patient_id, episode_discharge_ts)
    actor = _clinician_actor(http_request)
    record = save_decision(
        patient_id=patient_id,
        discharge_ts=episode_discharge_ts,
        decision=decision_request.decision,
        draft_plan=decision_request.draft_plan or assessment.draft_plan,
        actor=actor,
    )
    log_audit_event(
        "care_plan_decision_recorded",
        patient_id=patient_id,
        discharge_ts=episode_discharge_ts,
        decision=decision_request.decision,
        actor=actor,
    )
    if decision_request.decision == "approved":
        notification = notify_care_plan_decision(
            patient_id=patient_id,
            discharge_ts=episode_discharge_ts,
            message=f"Your care team ({actor}) approved your follow-up plan. Check the patient portal for details.",
        )
        log_audit_event(
            "notification_sent",
            patient_id=patient_id,
            discharge_ts=episode_discharge_ts,
            actor=actor,
            channel=notification["channel"],
            status=notification["status"],
        )
        writeback = record_fhir_writeback(assessment, record)
        log_audit_event(
            "fhir_writeback_recorded",
            patient_id=patient_id,
            discharge_ts=episode_discharge_ts,
            actor=actor,
            status=writeback["status"],
        )
    return DecisionRecord(**record, patient_ref=_patient_ref(patient_id))


@app.get("/patients/{patient_ref}/report", response_model=TransitionReport)
def patient_report(patient_ref: str, discharge_ts: str | None = None):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    patient_id = _resolve_patient_key(patient_ref)
    episode_discharge_ts = _latest_discharge_ts(patient_id, discharge_ts)
    decision = latest_decision(patient_id, episode_discharge_ts)
    if decision is None:
        return TransitionReport(
            status="pending_review",
            message="No clinician decision has been recorded yet.",
        )
    if decision["decision"] == "rejected":
        return TransitionReport(
            status="rejected_edit_required",
            message="Draft rejected. Edit the care plan, then send it for approval again.",
        )

    assessment = _assessment_cache_get(patient_id, episode_discharge_ts)
    if assessment is None:
        assessment = _generate_assessment(patient_id, episode_discharge_ts)
    report_markdown = build_transition_report(assessment, decision, DISCLAIMER)
    report_path = save_transition_report(patient_id, episode_discharge_ts, report_markdown)
    log_audit_event(
        "transition_report_generated",
        patient_id=patient_id,
        discharge_ts=episode_discharge_ts,
        actor=decision["actor"],
        report_path=str(report_path),
    )
    return TransitionReport(
        status="approved",
        message="Approved mock care-transition report generated.",
        report_markdown=report_markdown,
        report_path=str(report_path),
    )


@app.get("/patients/{patient_ref}/follow-up", response_model=FollowUpRecord | None, response_model_exclude={"patient_id"})
def patient_follow_up(patient_ref: str, discharge_ts: str | None = None):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    patient_id = _resolve_patient_key(patient_ref)
    episode_discharge_ts = _latest_discharge_ts(patient_id, discharge_ts)
    record = latest_follow_up(patient_id, episode_discharge_ts)
    return {**record, "patient_ref": _patient_ref(patient_id)} if record else None


@app.post("/patients/{patient_ref}/follow-up", response_model=FollowUpRecord, response_model_exclude={"patient_id"})
def save_patient_follow_up(
    patient_ref: str,
    body: FollowUpRequest,
    http_request: Request,
    discharge_ts: str | None = None,
):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    patient_id = _resolve_patient_key(patient_ref)
    episode_discharge_ts = _latest_discharge_ts(patient_id, discharge_ts)
    decision = latest_decision(patient_id, episode_discharge_ts)
    if decision is None or decision["decision"] != "approved":
        raise HTTPException(status_code=409, detail="Follow-up status requires an approved care plan")
    actor = _clinician_actor(http_request)
    record = save_follow_up_status(
        patient_id=patient_id,
        discharge_ts=episode_discharge_ts,
        status=body.status,
        actor=actor,
        note=body.note,
    )
    log_audit_event(
        "follow_up_status_recorded",
        patient_id=patient_id,
        discharge_ts=episode_discharge_ts,
        actor=actor,
        status=body.status,
    )
    return FollowUpRecord(**record, patient_ref=_patient_ref(patient_id))


@app.post("/patients/{patient_ref}/reminders", response_model=ReminderRecord, response_model_exclude={"patient_id"})
def schedule_patient_reminder(
    patient_ref: str,
    body: ReminderRequest,
    http_request: Request,
    discharge_ts: str | None = None,
):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")
    patient_id = _resolve_patient_key(patient_ref)
    episode_discharge_ts = _latest_discharge_ts(patient_id, discharge_ts)
    decision = latest_decision(patient_id, episode_discharge_ts)
    if decision is None or decision["decision"] != "approved":
        raise HTTPException(status_code=409, detail="Reminder requires an approved care plan")
    follow_up = latest_follow_up(patient_id, episode_discharge_ts)
    if follow_up is None:
        raise HTTPException(status_code=409, detail="Record follow-up status before scheduling a reminder")
    actor = _clinician_actor(http_request)
    remind_at = _validate_reminder_time(body.remind_at)
    message = body.message or "Follow up on the approved care-transition plan."
    record = schedule_notification_reminder(
        patient_id=patient_id,
        discharge_ts=episode_discharge_ts,
        remind_at=remind_at,
        message=message,
        actor=actor,
    )
    log_audit_event(
        "notification_reminder_scheduled",
        patient_id=patient_id,
        discharge_ts=episode_discharge_ts,
        actor=actor,
        remind_at=remind_at,
    )
    return ReminderRecord(**record, patient_ref=_patient_ref(patient_id))


@app.get("/care-plans")
def saved_care_plans(limit: int = 50):
    return [_redact_patient_record(record) for record in read_care_plans(limit=limit)]


@app.get("/notifications")
def saved_notifications(limit: int = 50):
    return [_redact_patient_record(record) for record in read_notifications(limit=limit)]


@app.get("/follow-ups")
def saved_follow_ups(limit: int = 50):
    return [_redact_patient_record(record) for record in read_follow_ups(limit=limit)]


@app.get("/reminders")
def saved_reminders(limit: int = 50):
    return [_redact_patient_record(record) for record in read_reminders(limit=limit)]


@app.get("/fhir-writebacks")
def saved_fhir_writebacks(limit: int = 50):
    return [_redact_patient_record(record) for record in read_fhir_writebacks(limit=limit)]


@app.get("/audit-events")
def saved_audit_events(
    limit: int = 100,
    patient_id: str | None = None,
    request_id: str | None = None,
):
    return [_redact_patient_record(record) for record in read_audit_events(limit=limit, patient_id=patient_id, request_id=request_id)]


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Wraps the tool-calling chat agent (src/agents/chat_agent.py)."""
    from src.agents.chat_agent import ask

    if _contains_patient_identifier(request.question):
        raise HTTPException(status_code=400, detail=PATIENT_ID_GUARDRAIL)

    question = request.question
    resolved = None
    if request.patient_name:
        resolved = _resolve_patient_name(request.patient_name)
        if resolved:
            question = (
                f"For patient {resolved['patient_name']}, already resolved internally as "
                f"patient_id {resolved['patient_id']}; use that patient_id for tools and do not search by surname again. "
                f"{question}"
            )
        else:
            question = f"For patient {request.patient_name}, {question}"

    try:
        result = _chat_fast_path(request.question, resolved) or ask(question)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    log_audit_event(
        "chat_completed",
        question=request.question,
        tool_calls=result["tool_calls"],
        answer=result["answer"],
    )

    return ChatResponse.model_validate(_redact_chat_payload(result))


@app.post("/tasks/precompute-assessments", status_code=202)
def launch_precompute_task(
    body: PrecomputeRequest | None = None,
    category: str | None = None,
    limit: int = 10,
    force: bool = False,
):
    if not _state:
        raise HTTPException(status_code=503, detail="Model not loaded")

    req_category = (body.category if body else None) or category or "high"
    req_limit = (body.limit if body else None) or limit or 10
    req_force = (body.force if body else None) or force or False

    categories = [c.strip() for c in req_category.split(",") if c.strip()] if req_category else None

    result = precompute_manager.start_background_precompute(
        get_candidates_fn=_queue_candidates,
        generate_fn=_generate_assessment,
        is_cached_fn=_is_assessment_ready,
        categories=categories,
        limit=req_limit,
        force=req_force,
    )
    return result


@app.get("/tasks/precompute-assessments/status", response_model=PrecomputeStatus)
def precompute_task_status():
    return precompute_manager.get_status()


@app.post("/intake/hl7-adt", response_model=IntakeResultResponse)
def intake_hl7_adt(request: HL7IntakeRequest):
    """
    Real-time HL7v2 intake gateway endpoint. Accepts ADT^A03 discharge messages,
    extracts patient context, derives risk features, computes 30-day readmission
    risk, triggers proactive precomputations, and publishes to Kafka.
    """
    try:
        result = process_realtime_intake(request.raw_message, trigger_precompute=True)
        return IntakeResultResponse(**result)
    except Exception as exc:
        logger.error("HL7 intake processing failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"HL7 intake error: {exc}")


@app.post("/intake/discharge-event", response_model=IntakeResultResponse)
def intake_discharge_event(request: DischargeEventTriggerRequest):
    """
    Structured real-time discharge event intake endpoint.
    Accepts patient/encounter discharge metadata or embedded FHIR resources.
    """
    try:
        event_dict = {
            "patient_id": request.patient_id,
            "discharge_ts": request.discharge_ts,
            "admit_ts": request.admit_ts,
            "encounter_id": request.encounter_id,
            "patient_name": request.patient_name,
            "admission_reason": request.admission_reason,
        }
        result = process_realtime_intake(
            adt_input=event_dict,
            fhir_bundle=request.fhir_bundle,
            trigger_precompute=True,
        )
        return IntakeResultResponse(**result)
    except Exception as exc:
        logger.error("Discharge event intake failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Discharge event error: {exc}")


@app.on_event("shutdown")
def shutdown_background_workers():
    consumer = _state.get("kafka_consumer")
    if consumer:
        logger.info("Stopping Kafka consumer on application shutdown...")
        consumer.stop()
