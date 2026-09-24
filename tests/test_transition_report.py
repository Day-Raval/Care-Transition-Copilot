import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.api import main
from src.api import production
from src.api.production import build_transition_report, save_transition_report


class TransitionReportTest(unittest.TestCase):
    def test_report_includes_actor_medications_and_plan(self):
        assessment = SimpleNamespace(
            patient_name="Jane Example",
            discharge_ts="2026-09-24T10:00:00",
            admission_reason="Heart failure",
            risk_category="high",
            risk_percentile=91.2,
            patient_context_summary="- [Medication] patient prescribed furosemide",
            critique_notes="No unsupported claims flagged.",
        )
        decision = {
            "actor": "Dr. Patel",
            "decided_at": "2026-09-24T11:00:00+00:00",
            "draft_plan": "Follow up with cardiology within 7 days.",
        }

        report = build_transition_report(assessment, decision, "Synthetic demo only.")

        self.assertIn("Dr. Patel", report)
        self.assertIn("furosemide", report)
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

                self.assertEqual(path.name, "care-transition-report__2026-08-09__994d6249.md")
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
