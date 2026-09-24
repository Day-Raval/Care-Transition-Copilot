import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.api import production


class DecisionIdempotencyTest(unittest.TestCase):
    def test_save_decision_keeps_one_record_per_episode(self):
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
