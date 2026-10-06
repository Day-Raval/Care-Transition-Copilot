import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from src.api import production
from src.api.main import app


class RequestIdTest(unittest.TestCase):
    def test_health_echoes_request_id_header(self):
        client = TestClient(app)
        response = client.get("/health", headers={"X-Request-ID": "demo-request-1"})

        self.assertEqual(response.headers["X-Request-ID"], "demo-request-1")

    def test_audit_event_includes_context_request_id(self):
        old_backend = os.environ.get("PERSISTENCE_BACKEND")
        original_path = production.AUDIT_LOG_PATH
        with TemporaryDirectory() as tmpdir:
            try:
                os.environ["PERSISTENCE_BACKEND"] = "local"
                production.AUDIT_LOG_PATH = Path(tmpdir) / "audit_log.jsonl"
                token = production.set_audit_request_id("demo-request-2")
                try:
                    production.log_audit_event("demo_event", patient_id="patient-1")
                finally:
                    production.reset_audit_request_id(token)

                events = production.read_audit_events(request_id="demo-request-2")
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event_type"], "demo_event")
                self.assertEqual(events[0]["request_id"], "demo-request-2")
            finally:
                production.AUDIT_LOG_PATH = original_path
                if old_backend is None:
                    os.environ.pop("PERSISTENCE_BACKEND", None)
                else:
                    os.environ["PERSISTENCE_BACKEND"] = old_backend
