import json
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
