"""ADR 009: OIDC resource-server checks. Every rejection path, plus the JWKS key path."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.api.auth import OidcVerifier
from tests.tokens import _KEY, AUDIENCE, ISSUER, PUBLIC_PEM, token

V = OidcVerifier(issuer=ISSUER, audience=AUDIENCE, key=PUBLIC_PEM)


def test_valid_token_yields_tenant_and_scopes() -> None:
    p = V.verify(token("acme", "pap:admin pap:ingest"))
    assert p is not None and p.tenant_id == "acme" and p.scopes == {"pap:admin", "pap:ingest"}


@pytest.mark.parametrize(
    "over",
    [
        {"exp": int(time.time()) - 120},  # expired beyond leeway
        {"aud": "someone-else"},
        {"iss": "https://evil.test"},
        {"tenant_id": None},  # claim missing
        {"tenant_id": "ACME; drop"},  # not a valid tenant id
        {"sub": None},
    ],
)
def test_rejected_claims(over: dict[str, Any]) -> None:
    assert V.verify(token(**over)) is None


def test_foreign_key_and_alg_tricks_are_rejected() -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(token(), options={"verify_signature": False})
    assert V.verify(jwt.encode(claims, other, algorithm="RS256")) is None
    assert V.verify(jwt.encode(claims, None, algorithm="none")) is None
    # Algorithm downgrade: an HMAC token is refused because only RS256 is allowed.
    forged = jwt.encode(claims, "x" * 32, algorithm="HS256")
    assert V.verify(forged) is None


def test_config_guards() -> None:
    with pytest.raises(ValueError):
        OidcVerifier(issuer=ISSUER, audience=AUDIENCE)  # no key source
    with pytest.raises(ValueError):
        OidcVerifier(issuer=ISSUER, audience=AUDIENCE, key="k", algorithms=["none"])
    with pytest.raises(ValueError):  # HS* + JWKS = forgeable with the public key
        OidcVerifier(issuer=ISSUER, audience=AUDIENCE, jwks_url="https://x", algorithms=["HS256"])


def test_jwks_path_uses_the_kid_selected_key() -> None:
    v = OidcVerifier(issuer=ISSUER, audience=AUDIENCE, jwks_url="https://idp.test/certs")
    seen: list[str] = []

    def fake(tok: str) -> Any:
        seen.append(jwt.get_unverified_header(tok).get("kid", ""))
        return SimpleNamespace(key=_KEY.public_key())

    v._jwks = SimpleNamespace(get_signing_key_from_jwt=fake)  # type: ignore[assignment]
    claims = jwt.decode(token(), options={"verify_signature": False})
    tok = jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "k1"})
    assert v.verify(tok) is not None and seen == ["k1"]
