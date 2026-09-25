import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.api import production


class DecisionIdempotencyTest(unittest.TestCase):
    def test_save_decision_keeps_one_record_per_episode(self):
        old_backend = os.environ.get("PERSISTENCE_BACKEND")
        os.environ["PERSISTENCE_BACKEND"] = "local"
        original_results_dir = production.RESULTS_DIR
        original_db_path = production.DECISIONS_DB_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            production.RESULTS_DIR = Path(tmpdir)
            production.DECISIONS_DB_PATH = Path(tmpdir) / "decisions.sqlite3"
            try:
                production.save_decision("patient-1", "2026-09-24", "approved", "first", actor="nurse_a")
                production.save_decision("patient-1", "2026-09-24", "rejected", "second", actor="physician_b")

                latest = production.latest_decision("patient-1", "2026-09-24")
                with sqlite3.connect(production.DECISIONS_DB_PATH) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM care_plan_decisions").fetchone()[0]

                self.assertEqual(count, 1)
                self.assertEqual(latest["decision"], "rejected")
                self.assertEqual(latest["actor"], "physician_b")
                self.assertEqual(latest["draft_plan"], "second")
            finally:
                production.RESULTS_DIR = original_results_dir
                production.DECISIONS_DB_PATH = original_db_path
                if old_backend is None:
                    os.environ.pop("PERSISTENCE_BACKEND", None)
                else:
                    os.environ["PERSISTENCE_BACKEND"] = old_backend

    def test_database_backend_persists_decisions_and_care_plans(self):
        old_backend = os.environ.get("PERSISTENCE_BACKEND")
        old_database_url = os.environ.get("DATABASE_URL")
        old_engine = production._engine
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "copilot.sqlite3"
            os.environ["PERSISTENCE_BACKEND"] = "database"
            os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
            production._engine = None
            try:
                production.save_decision("patient-1", "2026-09-24", "approved", "first", actor="nurse_a")
                production.save_decision("patient-1", "2026-09-24", "rejected", "second", actor="physician_b")
                production.save_care_plan({"patient_id": "patient-1", "discharge_ts": "2026-09-24", "draft_plan": "Plan"})

                latest = production.latest_decision("patient-1", "2026-09-24")
                plans = production.read_care_plans(limit=5)

                self.assertEqual(latest["decision"], "rejected")
                self.assertEqual(latest["actor"], "physician_b")
                self.assertEqual(latest["draft_plan"], "second")
                self.assertEqual(plans[0]["patient_id"], "patient-1")
                self.assertEqual(plans[0]["draft_plan"], "Plan")
            finally:
                if production._engine is not None:
                    production._engine.dispose()
                production._engine = old_engine
                if old_backend is None:
                    os.environ.pop("PERSISTENCE_BACKEND", None)
                else:
                    os.environ["PERSISTENCE_BACKEND"] = old_backend
                if old_database_url is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = old_database_url
