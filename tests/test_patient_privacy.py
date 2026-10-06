import json
import logging
import os
import unittest
from unittest.mock import patch

import pandas as pd

from src.api import main


class PatientPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.old_state = main._state.copy()
        main._state.clear()
        main._state.update(
            {
                "run_id": "run-1",
                "queue_df": pd.DataFrame(
                    [
                        {
                            "patient_id": "patient-1",
                            "patient_name": "Jane Example",
                            "discharge_ts": "2026-09-24",
                            "admission_reason": "Heart failure",
                        }
                    ]
                ),
                "reference_scores": pd.Series([1.0]),
            }
        )
        self.env = patch.dict(os.environ, {"PATIENT_REF_SALT": "test-salt"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        main._state.clear()
        main._state.update(self.old_state)

    def test_queue_uses_patient_ref_not_patient_id(self):
        item = main.patient_queue(limit=1)[0].model_dump()

        self.assertNotIn("patient_id", item)
        self.assertEqual(main._resolve_patient_key(item["patient_ref"]), "patient-1")

    def test_public_records_redact_nested_patient_ids(self):
        record = main._redact_patient_record(
            {
                "patient_id": "patient-1",
                "tool_calls": [{"arguments": {"patient_id": "patient-1"}, "result": "patient_id: patient-1"}],
            }
        )

        payload = json.dumps(record)
        self.assertNotIn("patient-1", payload)
        self.assertIn("patient_ref", payload)

    def test_chat_answer_masks_patient_identifiers(self):
        patient_ref = main._patient_ref("patient-1")

        record = main._redact_chat_payload(
            {
                "answer": f"Verify patient-1 / {patient_ref} / c15baf72-138a-3cd4-337c-793a89b5bcc0.",
                "tool_calls": [{"arguments": {"patient_id": "patient-1"}, "result": "patient_id: patient-1"}],
            }
        )

        self.assertNotIn("patient-1", record["answer"])
        self.assertNotIn(patient_ref, record["answer"])
        self.assertNotIn("c15baf72-138a-3cd4-337c-793a89b5bcc0", record["answer"])
        self.assertIn(main.MASKED_PATIENT_ID, record["answer"])

    def test_chat_rejects_patient_identifier_in_question(self):
        self.assertTrue(main._contains_patient_identifier("Review patient_id patient-1"))
        self.assertTrue(main._contains_patient_identifier(f"Review {main._patient_ref('patient-1')}"))
        self.assertFalse(main._contains_patient_identifier("Review Marvin's readmission risk"))

    def test_chat_uses_patient_name_as_structured_context(self):
        with (
            patch("src.agents.chat_agent.ask", return_value={"answer": "Done", "tool_calls": []}) as ask,
            patch.object(main, "log_audit_event"),
        ):
            response = main.chat(main.ChatRequest(question="Summarize meds", patient_name="Jane Example"))

        self.assertEqual(response.answer, "Done")
        ask.assert_called_once_with("For patient Jane Example, Summarize meds")

    def test_chat_request_rejects_patient_ref_field(self):
        with self.assertRaises(ValueError):
            main.ChatRequest.model_validate({"question": "Summarize meds", "patient_ref": main._patient_ref("patient-1")})

    def test_access_logs_are_disabled_by_default(self):
        self.assertTrue(logging.getLogger("uvicorn.access").disabled)

    def test_episode_lookup_errors_do_not_expose_patient_identifiers(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main._latest_discharge_ts("patient-1", "2026-01-01")

        detail = ctx.exception.detail
        self.assertNotIn("patient-1", detail)
        self.assertNotIn("2026-01-01", detail)
