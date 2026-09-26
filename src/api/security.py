"""OIDC access-token verification and role policy helpers."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient


class OIDCConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_jwk_set=True)


def verify_oidc_access_token(token: str) -> dict[str, Any]:
    issuer = os.getenv("OIDC_ISSUER", "").strip()
    audience = os.getenv("OIDC_AUDIENCE", "").strip()
    jwks_url = os.getenv("OIDC_JWKS_URL", "").strip()
    if not issuer or not audience or not jwks_url:
        raise OIDCConfigurationError(
            "OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL are required when AUTH_MODE=oidc"
        )

    signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
        options={"require": ["exp", "iat", "sub"]},
    )
    roles_claim = os.getenv("OIDC_ROLES_CLAIM", "roles").strip() or "roles"
    role_value = claims.get(roles_claim, [])
    if isinstance(role_value, str):
        roles = role_value.split()
    elif isinstance(role_value, list):
        roles = [role for role in role_value if isinstance(role, str)]
    else:
        roles = []

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise jwt.InvalidTokenError("Token subject is missing")
    return {"sub": subject, "roles": roles, "auth_mode": "oidc"}


def required_roles(method: str, path: str) -> set[str] | None:
    if path == "/predict":
        return None
    if path == "/drift-report":
        return {"data_scientist", "admin"}
    if method == "POST" and path.startswith("/patients/") and path.endswith("/decision"):
        return {"clinician", "admin"}
    if method == "GET" and path.startswith("/patients/") and path.endswith("/report"):
        return {"care_coordinator", "clinician", "admin"}
    if path == "/model-info":
        return {"care_coordinator", "clinician", "data_scientist", "admin"}
    if method == "GET" and path.startswith(("/patients", "/care-plans")):
        return {"care_coordinator", "clinician", "admin"}
    if method == "POST" and path == "/chat":
        return {"care_coordinator", "clinician", "admin"}
    return set()