"""
Small production-readiness helpers for the API layer.

These keep the local file/SQLite demo path, and switch to SQL-backed
tables when PERSISTENCE_BACKEND=database and DATABASE_URL are set.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine

from src.utils.config import Config

RESULTS_DIR = Path("results")
AUDIT_LOG_PATH = RESULTS_DIR / "audit_log.jsonl"
CARE_PLANS_PATH = RESULTS_DIR / "care_plans.jsonl"
DECISIONS_DB_PATH = RESULTS_DIR / "decisions.sqlite3"
REPORTS_DIR = Path("reports")
CHROMA_PATH = "data/processed/chroma_db"
DEFAULT_ACTOR = "demo_clinician"
DATABASE_BACKENDS = {"database", "postgres", "postgresql"}

_engine: Engine | None = None
_metadata = MetaData()

_decisions_table = Table(
    "care_plan_decisions",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("patient_id", String, nullable=False),
    Column("discharge_ts", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("decided_at", String, nullable=False),
    Column("actor", String, nullable=False, default=DEFAULT_ACTOR),
    Column("draft_plan", Text, nullable=False),
    CheckConstraint("decision IN ('approved', 'rejected')", name="ck_care_plan_decisions_decision"),
    UniqueConstraint("patient_id", "discharge_ts", name="uq_care_plan_decisions_episode"),
)

_audit_events_table = Table(
    "audit_events",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_json", Text, nullable=False),
)

_care_plans_table = Table(
    "care_plans",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", String, nullable=False),
    Column("patient_id", String, nullable=True),
    Column("discharge_ts", String, nullable=True),
    Column("payload_json", Text, nullable=False),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_requested() -> bool:
    return os.getenv("PERSISTENCE_BACKEND", "").strip().lower() in DATABASE_BACKENDS


def use_database() -> bool:
    if not database_requested():
        return False
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("PERSISTENCE_BACKEND=database requires DATABASE_URL")
    return True


def _db_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(os.environ["DATABASE_URL"], future=True)
    return _engine


def _json_dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=True, default=str)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(_json_dumps(record) + "\n")


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
    record = {"timestamp": utc_now(), "event_type": event_type, **payload}
    if use_database():
        init_decision_db()
        with _db_engine().begin() as conn:
            conn.execute(
                _audit_events_table.insert().values(
                    timestamp=record["timestamp"],
                    event_type=event_type,
                    payload_json=_json_dumps(record),
                )
            )
        return
    append_jsonl(AUDIT_LOG_PATH, record)


def save_care_plan(record: dict[str, Any]) -> None:
    saved = {"timestamp": utc_now(), **record}
    if use_database():
        init_decision_db()
        with _db_engine().begin() as conn:
            conn.execute(
                _care_plans_table.insert().values(
                    timestamp=saved["timestamp"],
                    patient_id=saved.get("patient_id"),
                    discharge_ts=saved.get("discharge_ts"),
                    payload_json=_json_dumps(saved),
                )
            )
        return
    append_jsonl(CARE_PLANS_PATH, saved)


def read_care_plans(limit: int = 100) -> list[dict[str, Any]]:
    if use_database():
        init_decision_db()
        with _db_engine().connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT payload_json
                    FROM care_plans
                    ORDER BY timestamp DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            return [json.loads(row.payload_json) for row in rows]
    return read_jsonl(CARE_PLANS_PATH, limit=limit)


def medication_context(summary: str) -> str:
    lines = [line.strip() for line in summary.splitlines() if "medication" in line.lower()]
    medications = []
    for line in lines:
        body = re.sub(r"^[-*\s\[\]A-Za-z]*medications?:\s*", "", line, flags=re.IGNORECASE)
        medications.extend(item.strip(" .") for item in body.split(";") if item.strip(" ."))
    return "\n".join(f"- {item}" for item in medications) if medications else "No medication-specific chart excerpts were returned."


def build_transition_report(assessment: Any, decision: dict[str, Any], disclaimer: str) -> str:
    return "\n\n".join(
        [
            "# Mock Care Transition Report",
            "\n".join(
                [
                    f"**Patient:** {assessment.patient_name}",
                    f"**Discharge timestamp:** {assessment.discharge_ts}",
                    f"**Admission reason:** {assessment.admission_reason}",
                    f"**Readmission risk:** {assessment.risk_category} ({assessment.risk_percentile:.1f} percentile)",
                    f"**Approved by:** {decision['actor']}",
                    f"**Approved at:** {decision['decided_at']}",
                ]
            ),
            "## Medication Context\n" + medication_context(assessment.patient_context_summary),
            "## Care Plan and Follow-Up Plan\n" + decision["draft_plan"],
            "## Independent Review Notes\n" + (assessment.critique_notes or "No critique notes recorded."),
            "## Disclaimer\n" + disclaimer,
        ]
    )


def public_report_path(patient_id: str, discharge_ts: str) -> Path:
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", discharge_ts)
    discharge_date = date_match.group(0) if date_match else "unknown-date"
    patient_slug = re.sub(r"[^a-zA-Z0-9]+", "-", patient_id).strip("-")[:8] or "patient"
    return REPORTS_DIR / f"care-transition-report__{discharge_date}__{patient_slug}.md"


def save_transition_report(patient_id: str, discharge_ts: str, report_markdown: str) -> Path:
    path = public_report_path(patient_id, discharge_ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_markdown, encoding="utf-8")
    return path


def init_decision_db() -> None:
    if use_database():
        _metadata.create_all(_db_engine())
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DECISIONS_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS care_plan_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                discharge_ts TEXT NOT NULL,
                decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
                decided_at TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'demo_clinician',
                draft_plan TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(care_plan_decisions)")}
        if "actor" not in columns:
            conn.execute("ALTER TABLE care_plan_decisions ADD COLUMN actor TEXT NOT NULL DEFAULT 'demo_clinician'")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_care_plan_decisions_patient_episode
            ON care_plan_decisions(patient_id, discharge_ts, decided_at DESC)
            """
        )
        conn.execute(
            """
            DELETE FROM care_plan_decisions
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM care_plan_decisions
                GROUP BY patient_id, discharge_ts
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_care_plan_decisions_one_per_episode
            ON care_plan_decisions(patient_id, discharge_ts)
            """
        )


def save_decision(
    patient_id: str,
    discharge_ts: str,
    decision: str,
    draft_plan: str,
    actor: str = DEFAULT_ACTOR,
) -> dict[str, Any]:
    actor = (actor or DEFAULT_ACTOR).strip() or DEFAULT_ACTOR
    record = {
        "patient_id": patient_id,
        "discharge_ts": discharge_ts,
        "decision": decision,
        "decided_at": utc_now(),
        "actor": actor,
        "draft_plan": draft_plan,
    }
    init_decision_db()
    if use_database():
        with _db_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO care_plan_decisions
                        (patient_id, discharge_ts, decision, decided_at, actor, draft_plan)
                    VALUES
                        (:patient_id, :discharge_ts, :decision, :decided_at, :actor, :draft_plan)
                    ON CONFLICT(patient_id, discharge_ts) DO UPDATE SET
                        decision = excluded.decision,
                        decided_at = excluded.decided_at,
                        actor = excluded.actor,
                        draft_plan = excluded.draft_plan
                    """
                ),
                record,
            )
        return record

    with sqlite3.connect(DECISIONS_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO care_plan_decisions
                (patient_id, discharge_ts, decision, decided_at, actor, draft_plan)
            VALUES
                (:patient_id, :discharge_ts, :decision, :decided_at, :actor, :draft_plan)
            ON CONFLICT(patient_id, discharge_ts) DO UPDATE SET
                decision = excluded.decision,
                decided_at = excluded.decided_at,
                actor = excluded.actor,
                draft_plan = excluded.draft_plan
            """,
            record,
        )
    return record


def latest_decision(patient_id: str, discharge_ts: str | None = None) -> dict[str, Any] | None:
    init_decision_db()
    sql = """
        SELECT patient_id, discharge_ts, decision, decided_at, actor, draft_plan
        FROM care_plan_decisions
        WHERE patient_id = :patient_id
    """
    params: dict[str, Any] = {"patient_id": patient_id}
    if discharge_ts is not None:
        sql += " AND discharge_ts = :discharge_ts"
        params["discharge_ts"] = discharge_ts
    sql += " ORDER BY decided_at DESC, id DESC LIMIT 1"

    if use_database():
        with _db_engine().connect() as conn:
            row = conn.execute(text(sql), params).mappings().first()
        return dict(row) if row else None

    sqlite_sql = sql.replace(":patient_id", "?").replace(":discharge_ts", "?")
    sqlite_params = [patient_id] + ([discharge_ts] if discharge_ts is not None else [])
    with sqlite3.connect(DECISIONS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sqlite_sql, sqlite_params).fetchone()
    return dict(row) if row else None


def runtime_dependency_report(cfg: Config) -> dict[str, Any]:
    target_csv = cfg.output_csv.replace(".csv", "_with_target.csv")
    checks = {
        "production_run_id": bool(cfg.production_run_id),
        "processed_dataset": os.path.exists(target_csv),
        "groq_api_key": bool(os.getenv("GROQ_API_KEY")),
        "vector_store": os.path.exists(CHROMA_PATH),
        "database_url": not database_requested() or bool(os.getenv("DATABASE_URL")),
    }
    missing = [name for name, ok in checks.items() if not ok]
    return {
        "ready": not missing,
        "checks": checks,
        "missing": missing,
    }
