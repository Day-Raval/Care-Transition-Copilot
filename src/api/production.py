"""
Small production-readiness helpers for the API layer.

These stay intentionally file-based because this project is still a
portfolio/demo app. They give us auditability and saved generated plans
without introducing a database before the app needs one.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.config import Config

RESULTS_DIR = Path("results")
AUDIT_LOG_PATH = RESULTS_DIR / "audit_log.jsonl"
CARE_PLANS_PATH = RESULTS_DIR / "care_plans.jsonl"
CHROMA_PATH = "data/processed/chroma_db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")


def read_jsonl(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records[:limit]


def log_audit_event(event_type: str, **payload: Any) -> None:
    append_jsonl(
        AUDIT_LOG_PATH,
        {
            "timestamp": utc_now(),
            "event_type": event_type,
            **payload,
        },
    )


def save_care_plan(record: dict[str, Any]) -> None:
    append_jsonl(CARE_PLANS_PATH, {"timestamp": utc_now(), **record})


def runtime_dependency_report(cfg: Config) -> dict[str, Any]:
    target_csv = cfg.output_csv.replace(".csv", "_with_target.csv")
    checks = {
        "production_run_id": bool(cfg.production_run_id),
        "processed_dataset": os.path.exists(target_csv),
        "groq_api_key": bool(os.getenv("GROQ_API_KEY")),
        "vector_store": os.path.exists(CHROMA_PATH),
    }
    missing = [name for name, ok in checks.items() if not ok]
    return {
        "ready": not missing,
        "checks": checks,
        "missing": missing,
    }
