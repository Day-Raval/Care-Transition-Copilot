import os
import threading
import time
import unittest
from unittest.mock import patch

from src.api import main


class AssessmentCacheTest(unittest.TestCase):
    def saved_plan(self, **overrides):
        record = {
            "patient_id": "patient-1",
            "discharge_ts": "2026-08-04",
            "patient_name": "Test Patient",
            "risk_score": 1.23,
            "risk_percentile": 90.0,
            "risk_category": "high",
            "admission_reason": "Test admission",
            "draft_plan": "Draft follow-up plan",
            "critique_notes": "STATUS: PASS",
        }
        record.update(overrides)
        return record

    def test_high_risk_saved_plan_without_context_is_not_full_assessment_cache(self):
        with (
            patch.dict(os.environ, {"CACHE_TTL_SECONDS": "0"}),
            patch.object(main, "latest_saved_care_plan", return_value=self.saved_plan()),
        ):
            self.assertIsNone(main._assessment_cache_get("patient-1", "2026-08-04"))

    def test_low_risk_saved_plan_without_context_can_be_cached(self):
        with (
            patch.dict(os.environ, {"CACHE_TTL_SECONDS": "0"}),
            patch.object(
                main,
                "latest_saved_care_plan",
                return_value=self.saved_plan(risk_category="low", risk_percentile=20.0),
            ),
        ):
            assessment = main._assessment_cache_get("patient-1", "2026-08-04")

        self.assertIsNotNone(assessment)
        self.assertEqual(assessment.risk_category, "low")
        self.assertEqual(assessment.patient_context_summary, "")

    def test_stale_memory_cache_without_context_is_ignored(self):
        key = ("patient-1", "2026-08-04")
        payload = self.saved_plan(patient_context_summary="")
        with (
            patch.dict(os.environ, {"CACHE_TTL_SECONDS": "600"}),
            patch.dict(
                main._state,
                {
                    "assessment_cache": {key: (time.monotonic() + 60, payload)},
                    "assessment_cache_lock": threading.Lock(),
                },
                clear=True,
            ),
            patch.object(main, "_redis_cache_client", return_value=None),
            patch.object(main, "latest_saved_care_plan", return_value=None),
        ):
            self.assertIsNone(main._assessment_cache_get(*key))
            self.assertNotIn(key, main._state["assessment_cache"])


if __name__ == "__main__":
    unittest.main()
