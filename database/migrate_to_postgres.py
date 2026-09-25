"""Migrate local MVP data (JSONL/SQLite files under results/) into Postgres.

Reuses the exact table definitions from src.api.production so the schema
this script creates is guaranteed to match what the running API expects
when PERSISTENCE_BACKEND=database.

Idempotent: safe to re-run. Decisions are upserted on (patient_id,
discharge_ts); care-plan and audit-event rows are skipped if an identical
(timestamp, payload) row already exists in Postgres. The original discharge
dataset (data/processed/discharge_records_with_target.csv) is reloaded in
full each run since it's a static source-of-truth export, not an
append-only log.

Usage:
    python database/migrate_to_postgres.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from src.api.production import (  # noqa: E402
    AUDIT_LOG_PATH,
    CARE_PLANS_PATH,
    DECISIONS_DB_PATH,
    _audit_events_table,
    _care_plans_table,
    _decisions_table,
    _metadata,
)

import os  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL")
DISCHARGE_RECORDS_PATH = PROJECT_ROOT / "data" / "processed" / "discharge_records_with_target.csv"
DISCHARGE_RECORDS_TABLE = "discharge_records_with_target"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def migrate_decisions(engine) -> tuple[int, int]:
    if not DECISIONS_DB_PATH.exists():
        return (0, 0)

    with sqlite3.connect(DECISIONS_DB_PATH) as sconn:
        sconn.row_factory = sqlite3.Row
        rows = sconn.execute(
            "SELECT patient_id, discharge_ts, decision, decided_at, actor, draft_plan "
            "FROM care_plan_decisions"
        ).fetchall()

    migrated = 0
    with engine.begin() as conn:
        for row in rows:
            stmt = pg_insert(_decisions_table).values(
                patient_id=row["patient_id"],
                discharge_ts=row["discharge_ts"],
                decision=row["decision"],
                decided_at=row["decided_at"],
                actor=row["actor"],
                draft_plan=row["draft_plan"],
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["patient_id", "discharge_ts"],
                set_={
                    "decision": stmt.excluded.decision,
                    "decided_at": stmt.excluded.decided_at,
                    "actor": stmt.excluded.actor,
                    "draft_plan": stmt.excluded.draft_plan,
                },
            )
            conn.execute(stmt)
            migrated += 1
    return (len(rows), migrated)


def migrate_jsonl(engine, path: Path, table) -> tuple[int, int]:
    records = read_jsonl(path)
    migrated = 0
    with engine.begin() as conn:
        for rec in records:
            payload_json = json.dumps(rec, ensure_ascii=True, default=str)
            exists = conn.execute(
                select(table.c.id).where(
                    table.c.timestamp == rec.get("timestamp"),
                    table.c.payload_json == payload_json,
                )
            ).first()
            if exists:
                continue
            values: dict[str, Any] = {"timestamp": rec.get("timestamp"), "payload_json": payload_json}
            if "patient_id" in table.c:
                values["patient_id"] = rec.get("patient_id")
            if "discharge_ts" in table.c:
                values["discharge_ts"] = rec.get("discharge_ts")
            if "event_type" in table.c:
                values["event_type"] = rec.get("event_type", "unknown")
            conn.execute(table.insert().values(**values))
            migrated += 1
    return (len(records), migrated)


def migrate_discharge_records(engine) -> int:
    if not DISCHARGE_RECORDS_PATH.exists():
        return 0

    df = pd.read_csv(DISCHARGE_RECORDS_PATH, parse_dates=["admit_ts", "discharge_ts"])
    df.to_sql(DISCHARGE_RECORDS_TABLE, engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        conn.execute(
            text(
                f'ALTER TABLE "{DISCHARGE_RECORDS_TABLE}" '
                'ADD PRIMARY KEY (encounter_id)'
            )
        )
    return len(df)


def main() -> int:
    if not DATABASE_URL:
        print("DATABASE_URL is not set. Check your .env file.")
        return 1

    engine = create_engine(DATABASE_URL, future=True)
    try:
        _metadata.create_all(engine)
        print("Schema ensured: care_plan_decisions, care_plans, audit_events")

        total, migrated = migrate_decisions(engine)
        print(f"Decisions:   {migrated} upserted out of {total} local rows")

        total, migrated = migrate_jsonl(engine, CARE_PLANS_PATH, _care_plans_table)
        print(f"Care plans:  {migrated} inserted out of {total} local rows")

        total, migrated = migrate_jsonl(engine, AUDIT_LOG_PATH, _audit_events_table)
        print(f"Audit log:   {migrated} inserted out of {total} local rows")

        loaded = migrate_discharge_records(engine)
        print(f"Discharge records with target: {loaded} rows reloaded into '{DISCHARGE_RECORDS_TABLE}'")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
