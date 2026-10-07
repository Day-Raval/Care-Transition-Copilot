import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.api import main
from src.api import production
from src.api.production import build_transition_report, record_fhir_writeback, save_transition_report


class TransitionReportTest(unittest.TestCase):
    def test_report_includes_actor_medications_and_plan(self):
        assessment = SimpleNamespace(
            patient_name="Jane Example",
            discharge_ts="2026-09-24T10:00:00",
            admission_reason="Heart failure",
            risk_category="high",
            risk_percentile=91.2,
            patient_context_summary="Medications: furosemide; lisinopril",
            critique_notes="No unsupported claims flagged.",
        )
        decision = {
            "actor": "Dr. Patel",
            "decided_at": "2026-09-24T11:00:00+00:00",
            "draft_plan": "Follow up with cardiology within 7 days.",
        }

        report = build_transition_report(assessment, decision, "Synthetic demo only.")

        self.assertIn("Dr. Patel", report)
        self.assertIn("- furosemide", report)
        self.assertIn("- lisinopril", report)
        self.assertIn("Follow up with cardiology", report)
        self.assertIn("Synthetic demo only.", report)

    def test_report_is_saved_with_public_filename(self):
        original_reports_dir = production.REPORTS_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            production.REPORTS_DIR = Path(tmpdir)
            try:
                path = save_transition_report(
                    "994d6249-48af-8064-c677-0fb58b1b59a8",
                    "2026-08-09 18:26:39+00:00",
                    "# Report",
                )

                self.assertEqual(path.name, "care-transition-report__2026-08-09__9c03687a.md")
                self.assertEqual(path.read_text(encoding="utf-8"), "# Report")
            finally:
                production.REPORTS_DIR = original_reports_dir

    def test_rejected_decision_returns_edit_required_status(self):
        old_state = main._state.copy()
        old_latest_discharge_ts = main._latest_discharge_ts
        old_latest_decision = main.latest_decision
        try:
            main._state.clear()
            main._state["ready"] = True
            main._latest_discharge_ts = lambda patient_id, discharge_ts=None: "2026-09-24"
            main.latest_decision = lambda patient_id, discharge_ts=None: {
                "decision": "rejected",
                "actor": "Dr. Patel",
                "decided_at": "2026-09-24T11:00:00+00:00",
            }

            response = main.patient_report("patient-1", "2026-09-24")

            self.assertEqual(response.status, "rejected_edit_required")
            self.assertIsNone(response.report_markdown)
        finally:
            main._state.clear()
            main._state.update(old_state)
            main._latest_discharge_ts = old_latest_discharge_ts
            main.latest_decision = old_latest_decision

    def test_fhir_writeback_stub_persists_care_plan_resource(self):
        original_path = production.FHIR_WRITEBACKS_PATH
        assessment = SimpleNamespace(risk_category="high", risk_percentile=91.2)
        decision = {
            "patient_id": "patient-1",
            "discharge_ts": "2026-09-24",
            "decided_at": "2026-09-24T11:00:00+00:00",
            "actor": "Dr. Patel",
            "draft_plan": "Follow up with cardiology within 7 days.",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            production.FHIR_WRITEBACKS_PATH = Path(tmpdir) / "fhir_writebacks.jsonl"
            try:
                record = record_fhir_writeback(assessment, decision)
                saved = production.read_fhir_writebacks(limit=10)

                self.assertEqual(record["status"], "stub_recorded")
                self.assertEqual(saved[0]["resource"]["resourceType"], "CarePlan")
                self.assertIn("Follow up with cardiology", saved[0]["resource"]["description"])
            finally:
                production.FHIR_WRITEBACKS_PATH = original_path

    def test_reminder_requires_follow_up_status(self):
        old_state = main._state.copy()
        old_resolve = main._resolve_patient_key
        old_latest_discharge_ts = main._latest_discharge_ts
        old_latest_decision = main.latest_decision
        old_latest_follow_up = main.latest_follow_up
        try:
            main._state.clear()
            main._state["ready"] = True
            main._resolve_patient_key = lambda patient_ref: "patient-1"
            main._latest_discharge_ts = lambda patient_id, discharge_ts=None: "2026-09-24"
            main.latest_decision = lambda patient_id, discharge_ts=None: {"decision": "approved"}
            main.latest_follow_up = lambda patient_id, discharge_ts=None: None

            with self.assertRaises(main.HTTPException) as caught:
                main.schedule_patient_reminder(
                    "patient-ref",
                    main.ReminderRequest(remind_at="2026-09-26T09:00"),
                    None,
                    "2026-09-24",
                )
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("Record follow-up status", caught.exception.detail)
        finally:
            main._state.clear()
            main._state.update(old_state)
            main._resolve_patient_key = old_resolve
            main._latest_discharge_ts = old_latest_discharge_ts
            main.latest_decision = old_latest_decision
            main.latest_follow_up = old_latest_follow_up

    def test_patient_queue_exposes_review_filter_flags(self):
        old_state = main._state.copy()
        old_saved_plan = main.latest_saved_care_plan
        old_latest_decision = main.latest_decision
        try:
            main._state.clear()
            main._state["queue_df"] = pd.DataFrame([
                {
                    "patient_id": "patient-1",
                    "patient_name": "Jane Example",
                    "discharge_ts": "2026-09-24",
                    "admission_reason": "Heart failure",
                },
                {
                    "patient_id": "patient-2",
                    "patient_name": "Sam Example",
                    "discharge_ts": "2026-09-25",
                    "admission_reason": "Pneumonia",
                },
            ])
            main._state["reference_scores"] = np.array([0.2, 0.9])
            main.latest_saved_care_plan = lambda patient_id, discharge_ts=None: (
                {"categories_with_no_match": ["medications"]} if patient_id == "patient-1" else None
            )
            main.latest_decision = lambda patient_id, discharge_ts=None: (
                {"decision": "approved"} if patient_id == "patient-1" else None
            )

            first = next(item for item in main._build_patient_queue_items() if item.patient_name == "Jane Example")
            second = next(item for item in main._build_patient_queue_items() if item.patient_name == "Sam Example")

            self.assertEqual(first.decision_status, "approved")
            self.assertTrue(first.precomputed)
            self.assertTrue(first.missing_evidence)
            self.assertFalse(first.needs_review)
            self.assertTrue(second.needs_review)
        finally:
            main._state.clear()
            main._state.update(old_state)
            main.latest_saved_care_plan = old_saved_plan
            main.latest_decision = old_latest_decision
