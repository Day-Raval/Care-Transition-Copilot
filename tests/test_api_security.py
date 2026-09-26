import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from src.api import main
from src.api import security


class OIDCAuthenticationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "OIDC_ISSUER": "https://identity.example.test/",
                "OIDC_AUDIENCE": "care-transition-api",
                "OIDC_JWKS_URL": "https://identity.example.test/.well-known/jwks.json",
                "OIDC_ROLES_CLAIM": "roles",
                "AUTH_MODE": "oidc",
                "API_KEY": "service-key",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        security._jwks_client.cache_clear()

    def make_token(self, roles):
        return jwt.encode(
            {
                "iss": "https://identity.example.test/",
                "aud": "care-transition-api",
                "sub": "clinician-123",
                "iat": 1_780_000_000,
                "exp": 1_900_000_000,
                "roles": roles,
            },
            self.private_key,
            algorithm="RS256",
        )

    def configure_jwks(self):
        key = type("SigningKey", (), {"key": self.private_key.public_key()})()
        client = type("JWKSClient", (), {"get_signing_key_from_jwt": lambda _, token: key})()
        return patch.object(security, "_jwks_client", return_value=client)

    def test_token_verification_extracts_subject_and_roles(self):
        token = self.make_token(["clinician"])
        with self.configure_jwks():
            claims = security.verify_oidc_access_token(token)
        self.assertEqual(claims["sub"], "clinician-123")
        self.assertEqual(claims["roles"], ["clinician"])
        self.assertEqual(claims["auth_mode"], "oidc")

    def test_unknown_routes_are_denied_by_default(self):
        self.assertEqual(security.required_roles("GET", "/internal/debug"), set())

    def test_coordinator_can_read_patient_data_but_not_drift_metrics(self):
        self.assertIn("care_coordinator", security.required_roles("GET", "/patients"))
        self.assertNotIn("care_coordinator", security.required_roles("GET", "/drift-report"))

    def test_decision_request_does_not_accept_client_actor(self):
        decision = main.DecisionRequest.model_validate(
            {"decision": "approved", "actor": "forged-clinician"}
        )
        self.assertFalse(hasattr(decision, "actor"))

    def test_oidc_actor_comes_from_verified_subject_not_request_header(self):
        from starlette.requests import Request

        request = Request(
            {
                "type": "http",
                "headers": [(b"x-clinician-id", b"forged-clinician")],
                "state": {"principal": {"sub": "verified-clinician", "auth_mode": "oidc"}},
            }
        )
        self.assertEqual(main._clinician_actor(request), "verified-clinician")

    def test_coordinator_cannot_record_decision(self):
        client = TestClient(main.app)
        token = self.make_token(["care_coordinator"])
        with self.configure_jwks():
            response = client.post(
                "/patients/patient-1/decision",
                headers={"Authorization": f"Bearer {token}"},
                json={"decision": "approved", "actor": "forged-clinician"},
            )
        self.assertEqual(response.status_code, 403)

    def test_clinician_decision_is_attributed_to_verified_subject(self):
        client = TestClient(main.app)
        token = self.make_token(["clinician"])
        stored_decision = {
            "patient_id": "patient-1",
            "discharge_ts": "2026-09-24",
            "decision": "approved",
            "decided_at": "2026-09-26T12:00:00+00:00",
            "actor": "clinician-123",
            "draft_plan": "Follow-up plan",
        }
        with (
            patch.dict(main._state, {"test_state": True}, clear=True),
            patch.object(main, "_latest_discharge_ts", return_value="2026-09-24"),
            patch.object(main, "_assessment_cache_get", return_value=SimpleNamespace(draft_plan="Follow-up plan")),
            patch.object(main, "save_decision", return_value=stored_decision) as save_decision,
            patch.object(main, "log_audit_event"),
            self.configure_jwks(),
        ):
            response = client.post(
                "/patients/patient-1/decision",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Clinician-ID": "forged-header-user",
                },
                json={"decision": "approved", "actor": "forged-body-user"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["actor"], "clinician-123")
        self.assertEqual(save_decision.call_args.kwargs["actor"], "clinician-123")

    def test_health_remains_public_and_predict_requires_service_key(self):
        client = TestClient(main.app)
        self.assertNotEqual(client.get("/health").status_code, 401)
        self.assertEqual(client.post("/predict", json={}).status_code, 401)
        response = client.post("/predict", headers={"X-API-Key": "service-key"}, json={})
        self.assertNotEqual(response.status_code, 401)