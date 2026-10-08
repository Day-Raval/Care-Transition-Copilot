import os
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.observability import route_label, safe_trace_inputs


class ObservabilityTest(unittest.TestCase):
    def test_metrics_route_does_not_require_api_key(self):
        client = TestClient(app)
        response = client.get("/metrics")

        self.assertIn(response.status_code, {200, 503})
        self.assertNotEqual(response.status_code, 401)

    def test_route_label_redacts_patient_ref_values(self):
        request = Mock()
        request.scope = {"route": None}
        request.url.path = "/patients/abc123def4567890/assessment"

        self.assertEqual(route_label(request), "/patients/{patient_ref}/assessment")

    def test_safe_trace_inputs_drops_empty_metadata_without_raw_payloads(self):
        payload = safe_trace_inputs(
            patient_ref="abc123def4567890",
            question=None,
            question_chars=42,
        )

        self.assertEqual(payload, {"patient_ref": "abc123def4567890", "question_chars": 42})
        self.assertNotIn("question", payload)

    def test_metrics_do_not_require_auth_mode_configuration(self):
        old_auth_mode = os.environ.get("AUTH_MODE")
        try:
            os.environ["AUTH_MODE"] = "not-valid"
            client = TestClient(app)
            response = client.get("/metrics")
        finally:
            if old_auth_mode is None:
                os.environ.pop("AUTH_MODE", None)
            else:
                os.environ["AUTH_MODE"] = old_auth_mode

        self.assertIn(response.status_code, {200, 503})
        self.assertNotIn("AUTH_MODE must be", response.text)
