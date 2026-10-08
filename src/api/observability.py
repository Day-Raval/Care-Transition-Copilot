from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager, nullcontext
from typing import Any

try:
    from prometheus_client import Counter, Histogram, make_asgi_app
except ImportError:  # pragma: no cover - exercised only before requirements are installed
    Counter = Histogram = make_asgi_app = None

try:
    import langsmith as ls
except ImportError:  # pragma: no cover - exercised only before requirements are installed
    ls = None


class _NoopMetric:
    def labels(self, *args, **kwargs):
        return self

    def inc(self, *args, **kwargs):
        return None

    def observe(self, *args, **kwargs):
        return None


if Counter and Histogram:
    HTTP_REQUESTS = Counter(
        "care_transition_http_requests_total",
        "HTTP requests handled by the API.",
        ("method", "route", "status"),
    )
    HTTP_DURATION = Histogram(
        "care_transition_http_request_duration_seconds",
        "HTTP request duration in seconds.",
        ("method", "route"),
    )
    RISK_PREDICTIONS = Counter(
        "care_transition_risk_predictions_total",
        "Served risk predictions by category.",
        ("risk_category",),
    )
    AGENT_RUNS = Counter(
        "care_transition_agent_runs_total",
        "Agent runs by entrypoint and status.",
        ("entrypoint", "status"),
    )
    AGENT_DURATION = Histogram(
        "care_transition_agent_run_duration_seconds",
        "Agent run duration in seconds.",
        ("entrypoint",),
    )
    AGENT_TOOL_CALLS = Counter(
        "care_transition_agent_tool_calls_total",
        "Agent tool calls by tool and status.",
        ("tool", "status"),
    )
    LLM_CALLS = Counter(
        "care_transition_llm_calls_total",
        "LLM calls by agent, model, and status.",
        ("agent", "model", "status"),
    )
    LLM_DURATION = Histogram(
        "care_transition_llm_call_duration_seconds",
        "LLM call duration in seconds.",
        ("agent", "model"),
    )
else:
    HTTP_REQUESTS = HTTP_DURATION = RISK_PREDICTIONS = _NoopMetric()
    AGENT_RUNS = AGENT_DURATION = AGENT_TOOL_CALLS = _NoopMetric()
    LLM_CALLS = LLM_DURATION = _NoopMetric()


def make_metrics_route():
    if make_asgi_app:
        return make_asgi_app()

    async def missing_metrics(scope, receive, send):
        body = b"# prometheus_client is not installed\n"
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"text/plain; version=0.0.4")],
        })
        await send({"type": "http.response.body", "body": body})

    return missing_metrics


def route_label(request) -> str:
    route = getattr(request.scope.get("route"), "path", None) or request.url.path
    route = re.sub(r"/patients/[^/]+", "/patients/{patient_ref}", route)
    route = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "{uuid}",
        route,
    )
    route = re.sub(r"\b[0-9a-fA-F]{16}\b", "{patient_ref}", route)
    return route


def record_http_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    if route == "/metrics":
        return
    status = str(status_code)
    HTTP_REQUESTS.labels(method, route, status).inc()
    HTTP_DURATION.labels(method, route).observe(duration_seconds)


def record_risk_prediction(risk_category: str) -> None:
    RISK_PREDICTIONS.labels(risk_category).inc()


@contextmanager
def agent_run(entrypoint: str):
    started = time.perf_counter()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        AGENT_RUNS.labels(entrypoint, status).inc()
        AGENT_DURATION.labels(entrypoint).observe(time.perf_counter() - started)


@contextmanager
def llm_call(agent: str, model: str):
    started = time.perf_counter()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        LLM_CALLS.labels(agent, model, status).inc()
        LLM_DURATION.labels(agent, model).observe(time.perf_counter() - started)


@contextmanager
def tool_call(tool: str):
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        AGENT_TOOL_CALLS.labels(tool, status).inc()


def safe_trace_inputs(**metadata: Any) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if value is not None}


@contextmanager
def trace_block(name: str, run_type: str = "chain", inputs: dict[str, Any] | None = None):
    if not ls or os.getenv("LANGSMITH_TRACING", "").lower() != "true":
        with nullcontext() as ctx:
            yield ctx
        return
    with ls.trace(name, run_type=run_type, inputs=inputs or {}) as run:
        yield run
