"""Verify local results/ data was fully migrated into Postgres.

Compares row counts and spot-checks that every local record has a matching
row in the corresponding Postgres table.

Usage:
    python database/verify_migration.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select, text

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
    _model_predictions_table,
    sqlalchemy_database_url,
)

import os  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL")
DISCHARGE_RECORDS_PATH = PROJECT_ROOT / "data" / "processed" / "discharge_records_with_target.csv"
PREDICTION_LOG_PATH = PROJECT_ROOT / "results" / "operations" / "prediction_log.csv"
DISCHARGE_RECORDS_TABLE = "discharge_records_with_target"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def check_decisions(engine) -> bool:
    if not DECISIONS_DB_PATH.exists():
        print("Decisions: no local sqlite3 file found, skipping.")
        return True

    with sqlite3.connect(DECISIONS_DB_PATH) as sconn:
        sconn.row_factory = sqlite3.Row
        local_rows = sconn.execute(
            "SELECT patient_id, discharge_ts, decision FROM care_plan_decisions"
        ).fetchall()

    missing = []
    with engine.connect() as conn:
        for row in local_rows:
            match = conn.execute(
                select(_decisions_table.c.decision).where(
                    _decisions_table.c.patient_id == row["patient_id"],
                    _decisions_table.c.discharge_ts == row["discharge_ts"],
                )
            ).first()
            if match is None or match[0] != row["decision"]:
                missing.append((row["patient_id"], row["discharge_ts"]))

    ok = not missing
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] Decisions: {len(local_rows)} local rows, {len(missing)} missing/mismatched in Postgres")
    for patient_id, discharge_ts in missing:
        print(f"    missing: patient_id={patient_id} discharge_ts={discharge_ts}")
    return ok


def check_jsonl(engine, path: Path, table, label: str) -> bool:
    records = read_jsonl(path)
    missing = []
    with engine.connect() as conn:
        for rec in records:
            payload_json = json.dumps(rec, ensure_ascii=True, default=str)
            match = conn.execute(
                select(table.c.id).where(
                    table.c.timestamp == rec.get("timestamp"),
                    table.c.payload_json == payload_json,
                )
            ).first()
            if match is None:
                missing.append(rec.get("timestamp"))

    ok = not missing
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}: {len(records)} local rows, {len(missing)} missing in Postgres")
    for ts in missing:
        print(f"    missing: timestamp={ts}")
    return ok


def check_discharge_records(engine) -> bool:
    if not DISCHARGE_RECORDS_PATH.exists():
        print("Discharge records: no local CSV found, skipping.")
        return True

    local_ids = set(pd.read_csv(DISCHARGE_RECORDS_PATH, usecols=["encounter_id"])["encounter_id"])
    with engine.connect() as conn:
        pg_ids = {
            row[0]
            for row in conn.execute(text(f'SELECT encounter_id FROM "{DISCHARGE_RECORDS_TABLE}"'))
        }

    missing = local_ids - pg_ids
    ok = not missing
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] Discharge records: {len(local_ids)} local rows, {len(missing)} missing in Postgres")
    for encounter_id in list(missing)[:10]:
        print(f"    missing: encounter_id={encounter_id}")
    return ok


def check_prediction_log(engine) -> bool:
    if not PREDICTION_LOG_PATH.exists():
        print("Prediction log: no local CSV found, skipping.")
        return True

    df = pd.read_csv(PREDICTION_LOG_PATH)
    expected = set()
    for rec in df.to_dict(orient="records"):
        timestamp = rec.pop("timestamp", None)
        risk_score = rec.pop("risk_score", None)
        rec.pop("model_run_id", None)
        payload_json = json.dumps({k: v for k, v in rec.items() if pd.notna(v)}, ensure_ascii=True, default=str)
        expected.add((timestamp, risk_score, payload_json))

    with engine.connect() as conn:
        found = {
            tuple(row)
            for row in conn.execute(
                select(
                    _model_predictions_table.c.timestamp,
                    _model_predictions_table.c.risk_score,
                    _model_predictions_table.c.feature_values_json,
                )
            )
        }

    missing = expected - found
    ok = not missing
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] Prediction log: {len(expected)} unique local rows, {len(missing)} missing in Postgres")
    for timestamp, risk_score, _ in list(missing)[:10]:
        print(f"    missing: timestamp={timestamp} risk_score={risk_score}")
    return ok


def print_table_counts(engine) -> None:
    with engine.connect() as conn:
        for table in (_decisions_table, _care_plans_table, _audit_events_table):
            count = conn.execute(select(func.count()).select_from(table)).scalar_one()
            print(f"  {table.name}: {count} rows in Postgres")
        count = conn.execute(select(func.count()).select_from(_model_predictions_table)).scalar_one()
        print(f"  {_model_predictions_table.name}: {count} rows in Postgres")
        result = conn.execute(text(f'SELECT to_regclass(\'"{DISCHARGE_RECORDS_TABLE}"\')')).scalar()
        if result is not None:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{DISCHARGE_RECORDS_TABLE}"')).scalar_one()
            print(f"  {DISCHARGE_RECORDS_TABLE}: {count} rows in Postgres")


def main() -> int:
    if not DATABASE_URL:
        print("DATABASE_URL is not set. Check your .env file.")
        return 1

    engine = create_engine(sqlalchemy_database_url(DATABASE_URL), future=True)
    try:
        print("Postgres table counts:")
        print_table_counts(engine)
        print()

        results = [
            check_decisions(engine),
            check_jsonl(engine, CARE_PLANS_PATH, _care_plans_table, "Care plans"),
            check_jsonl(engine, AUDIT_LOG_PATH, _audit_events_table, "Audit log"),
            check_discharge_records(engine),
            check_prediction_log(engine),
        ]
        return 0 if all(results) else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
