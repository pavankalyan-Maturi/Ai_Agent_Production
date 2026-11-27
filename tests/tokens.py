"""Mint RS256 OIDC access tokens for API tests (stands in for Keycloak)."""

from __future__ import annotations

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from apps.api.settings import Settings

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
ISSUER, AUDIENCE = "https://idp.test/realms/pap", "pap-api"


def token(tenant: str = "acme", scope: str = "pap:admin", **over: Any) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "tenant_id": tenant,
        "scope": scope,
        "exp": int(time.time()) + 300,
    } | over
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, _KEY, algorithm="RS256")


def bearer(tenant: str = "acme", scope: str = "pap:admin", **over: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(tenant, scope, **over)}"}


def auth_settings(**kw: Any) -> Settings:
    return Settings(
        oidc_issuer=ISSUER, oidc_audience=AUDIENCE, oidc_key=SecretStr(PUBLIC_PEM), **kw
    )
