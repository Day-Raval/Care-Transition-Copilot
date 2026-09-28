import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.api import drift_monitor
from src.api import production
from src.data_services import kafka_events
from src.utils.config import Config


class FakeKafkaProducer:
    def __init__(self):
        self.messages = []
        self.flushed = False

    def produce(self, topic, key=None, value=None, callback=None):
        self.messages.append({"topic": topic, "key": key, "value": value})

    def poll(self, timeout):
        self.polled = timeout

    def flush(self, timeout):
        self.flushed = True


class DataServicesProductionTest(unittest.TestCase):
    def setUp(self):
        self.old_backend = os.environ.get("PERSISTENCE_BACKEND")
        self.old_database_url = os.environ.get("DATABASE_URL")
        self.old_engine = production._engine

    def tearDown(self):
        if production._engine is not None:
            production._engine.dispose()
        production._engine = self.old_engine
        if self.old_backend is None:
            os.environ.pop("PERSISTENCE_BACKEND", None)
        else:
            os.environ["PERSISTENCE_BACKEND"] = self.old_backend
        if self.old_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.old_database_url

    def use_temp_database(self, tmpdir: str) -> Path:
        db_path = Path(tmpdir) / "copilot.sqlite3"
        os.environ["PERSISTENCE_BACKEND"] = "database"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
        production._engine = None
        return db_path

    def test_database_backend_reads_discharge_records_from_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.use_temp_database(tmpdir)
            df = pd.DataFrame(
                [
                    {
                        "patient_id": "patient-1",
                        "patient_name": "Jane Example",
                        "encounter_id": "enc-1",
                        "discharge_ts": "2026-09-24",
                        "outcome": "POSITIVE",
                        "event_observed": 1,
                    }
                ]
            )
            df.to_sql(production.DISCHARGE_RECORDS_TABLE, production._db_engine(), index=False, if_exists="replace")

            loaded = production.load_discharge_records(Config(output_csv=str(Path(tmpdir) / "missing.csv")))

            self.assertEqual(loaded.iloc[0]["patient_id"], "patient-1")
            self.assertTrue(production.runtime_dependency_report(Config(production_run_id="run-1"))["checks"]["processed_dataset"])

    def test_database_backend_populates_typed_event_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self.use_temp_database(tmpdir)

            production.log_audit_event(
                "assessment_generated",
                patient_id="patient-1",
                discharge_ts="2026-09-24",
                actor="clinician-1",
                request_id="req-1",
                model_run_id="run-1",
            )
            production.save_care_plan(
                {
                    "patient_id": "patient-1",
                    "discharge_ts": "2026-09-24",
                    "risk_category": "high",
                    "model_run_id": "run-1",
                    "draft_plan": "Plan",
                }
            )

            with sqlite3.connect(db_path) as conn:
                audit = conn.execute(
                    "SELECT patient_id, discharge_ts, actor, request_id, model_run_id FROM audit_events"
                ).fetchone()
                plan = conn.execute(
                    "SELECT patient_id, discharge_ts, risk_category, model_run_id FROM care_plans"
                ).fetchone()

            self.assertEqual(audit, ("patient-1", "2026-09-24", "clinician-1", "req-1", "run-1"))
            self.assertEqual(plan, ("patient-1", "2026-09-24", "high", "run-1"))

    def test_drift_monitor_uses_database_prediction_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.use_temp_database(tmpdir)

            drift_monitor.log_prediction({"age_at_discharge": 72, "medication_count": 8}, 1.25, model_run_id="run-1")
            recent = drift_monitor.load_recent_predictions()

            self.assertEqual(len(recent), 1)
            self.assertEqual(recent.iloc[0]["model_run_id"], "run-1")
            self.assertEqual(recent.iloc[0]["age_at_discharge"], 72)


class VectorStoreSafetyTest(unittest.TestCase):
    def test_existing_vector_store_requires_explicit_rebuild(self):
        from src.embeddings import build_vector_store

        old_path = build_vector_store.CHROMA_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            build_vector_store.CHROMA_PATH = str(Path(tmpdir) / "chroma_db")
            Path(build_vector_store.CHROMA_PATH).mkdir()
            try:
                with self.assertRaisesRegex(RuntimeError, "rebuild=True"):
                    build_vector_store.build_vector_store(str(Path(tmpdir) / "notes.jsonl"))
            finally:
                build_vector_store.CHROMA_PATH = old_path


class KafkaEventsTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {name: os.environ.get(name) for name in [
            "KAFKA_ENABLED",
            "KAFKA_BOOTSTRAP_SERVERS",
            "KAFKA_TOPIC_AUDIT_EVENTS",
        ]}
        self.old_producer = kafka_events._producer

    def tearDown(self):
        kafka_events._producer = self.old_producer
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def enable_fake_kafka(self) -> FakeKafkaProducer:
        os.environ["KAFKA_ENABLED"] = "true"
        os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
        os.environ["KAFKA_TOPIC_AUDIT_EVENTS"] = "test.audit"
        fake = FakeKafkaProducer()
        kafka_events._producer = fake
        return fake

    def test_publish_event_is_disabled_by_default(self):
        os.environ["KAFKA_ENABLED"] = "false"
        kafka_events._producer = FakeKafkaProducer()

        published = kafka_events.publish_event("topic", "event", {"patient_id": "patient-1"})

        self.assertFalse(published)

    def test_publish_event_wraps_payload_in_envelope(self):
        fake = self.enable_fake_kafka()

        published = kafka_events.publish_audit_event("assessment_generated", {"patient_id": "patient-1"})

        self.assertTrue(published)
        self.assertEqual(fake.messages[0]["topic"], "test.audit")
        self.assertEqual(fake.messages[0]["key"], "patient-1")
        envelope = json.loads(fake.messages[0]["value"].decode("utf-8"))
        self.assertEqual(envelope["schema_version"], 1)
        self.assertEqual(envelope["event_type"], "assessment_generated")
        self.assertEqual(envelope["payload"]["patient_id"], "patient-1")

    def test_log_audit_event_publishes_to_kafka_when_enabled(self):
        fake = self.enable_fake_kafka()
        old_backend = os.environ.get("PERSISTENCE_BACKEND")
        original_audit_path = production.AUDIT_LOG_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["PERSISTENCE_BACKEND"] = "local"
            production.AUDIT_LOG_PATH = Path(tmpdir) / "audit.jsonl"
            try:
                production.log_audit_event("chat_completed", patient_id="patient-1", request_id="req-1")
            finally:
                production.AUDIT_LOG_PATH = original_audit_path
                if old_backend is None:
                    os.environ.pop("PERSISTENCE_BACKEND", None)
                else:
                    os.environ["PERSISTENCE_BACKEND"] = old_backend

        envelope = json.loads(fake.messages[0]["value"].decode("utf-8"))
        self.assertEqual(envelope["event_type"], "chat_completed")
        self.assertEqual(envelope["payload"]["request_id"], "req-1")
