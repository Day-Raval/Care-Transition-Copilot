import unittest

from fastapi.testclient import TestClient

from src.api.main import app


class RequestIdTest(unittest.TestCase):
    def test_health_echoes_request_id_header(self):
        client = TestClient(app)
        response = client.get("/health", headers={"X-Request-ID": "demo-request-1"})

        self.assertEqual(response.headers["X-Request-ID"], "demo-request-1")
