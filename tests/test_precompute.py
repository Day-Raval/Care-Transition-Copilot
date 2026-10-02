import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.api import production
from src.api.precompute import PrecomputeManager


class PrecomputeTest(unittest.TestCase):
    def test_patient_event_deduplication(self):
        mgr = PrecomputeManager()
        evt1, is_creator1 = mgr.get_patient_event("patient-A", "2026-10-01")
        self.assertTrue(is_creator1)

        # Second concurrent caller gets the same event and is NOT the creator
        evt2, is_creator2 = mgr.get_patient_event("patient-A", "2026-10-01")
        self.assertFalse(is_creator2)
        self.assertIs(evt1, evt2)
        self.assertFalse(evt1.is_set())

        # Finish event
        mgr.finish_patient_event("patient-A", "2026-10-01")
        self.assertTrue(evt1.is_set())

        # Next call becomes creator again
        evt3, is_creator3 = mgr.get_patient_event("patient-A", "2026-10-01")
        self.assertTrue(is_creator3)
        self.assertIsNot(evt1, evt3)

    def test_background_precompute_worker(self):
        mgr = PrecomputeManager()

        candidates = [
            {"patient_id": "p1", "discharge_ts": "2026-09-01", "risk_category": "high"},
            {"patient_id": "p2", "discharge_ts": "2026-09-02", "risk_category": "high"},
            {"patient_id": "p3", "discharge_ts": "2026-09-03", "risk_category": "low"},
        ]

        cached_set = {"p1"}
        generated = []

        def get_candidates():
            return candidates

        def is_cached(p_id, d_ts):
            return p_id in cached_set

        def generate(p_id, d_ts):
            generated.append(p_id)

        res = mgr.start_background_precompute(
            get_candidates_fn=get_candidates,
            generate_fn=generate,
            is_cached_fn=is_cached,
            categories=["high"],
            limit=5,
            force=False,
        )

        self.assertTrue(res["started"])

        # Wait for thread to finish
        deadline = time.time() + 5
        while mgr.is_running() and time.time() < deadline:
            time.sleep(0.05)

        self.assertFalse(mgr.is_running())
        status = mgr.get_status()
        self.assertEqual(status["total"], 2)  # only p1 and p2 are high
        self.assertEqual(status["skipped"], 1)  # p1 was already cached
        self.assertEqual(status["completed"], 1)  # p2 was generated
        self.assertIn("p2", generated)
        self.assertNotIn("p1", generated)

    def test_latest_saved_care_plan_local_and_db(self):
        old_backend = os.environ.get("PERSISTENCE_BACKEND")
        os.environ["PERSISTENCE_BACKEND"] = "local"
        original_results_dir = production.RESULTS_DIR
        original_care_plans_path = production.CARE_PLANS_PATH

        with tempfile.TemporaryDirectory() as tmpdir:
            production.RESULTS_DIR = Path(tmpdir)
            production.CARE_PLANS_PATH = Path(tmpdir) / "care_plans.jsonl"
            try:
                production.save_care_plan({
                    "patient_id": "patient-99",
                    "discharge_ts": "2026-08-10",
                    "patient_name": "Test Subject",
                    "draft_plan": "Draft 1",
                    "risk_category": "high",
                })
                production.save_care_plan({
                    "patient_id": "patient-99",
                    "discharge_ts": "2026-08-10",
                    "patient_name": "Test Subject",
                    "draft_plan": "Draft 2 Updated",
                    "risk_category": "high",
                })

                saved = production.latest_saved_care_plan("patient-99", "2026-08-10")
                self.assertIsNotNone(saved)
                self.assertEqual(saved["draft_plan"], "Draft 2 Updated")

                # Query non-existent patient
                self.assertIsNone(production.latest_saved_care_plan("patient-unknown", "2026-08-10"))
            finally:
                production.RESULTS_DIR = original_results_dir
                production.CARE_PLANS_PATH = original_care_plans_path
                if old_backend is None:
                    os.environ.pop("PERSISTENCE_BACKEND", None)
                else:
                    os.environ["PERSISTENCE_BACKEND"] = old_backend


if __name__ == "__main__":
    unittest.main()
