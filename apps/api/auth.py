"""OIDC bearer-token auth for the API (ADR 009). Replaces the Phase 3 admin key.

The API is an OAuth resource server: it verifies access tokens an OIDC provider issued
(Keycloak locally and on EKS, ADR 009) and never issues tokens itself. A token must carry our
issuer, `aud` = PAP_OIDC_AUDIENCE, `exp`, `sub` and a `tenant_id` claim (a Keycloak protocol
mapper). The tenant always comes from the verified token, never from a header or a parameter.

Keys: production fetches the issuer's JWKS (PAP_OIDC_JWKS_URL, cached by PyJWKClient); tests
and keyless dev pass one PEM public key or an HS256 secret (PAP_OIDC_KEY). With neither set,
protected routes answer 503: auth is never silently off.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException, Request

ADMIN, INGEST = "pap:admin", "pap:ingest"
TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ASYMMETRIC = {"RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"}


@dataclass(frozen=True)
class Principal:
    sub: str
    tenant_id: str
    scopes: frozenset[str]


class OidcVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key: str | None = None,
        jwks_url: str | None = None,
        algorithms: Sequence[str] = ("RS256",),
        leeway: int = 30,
    ) -> None:
        if (key is None) == (jwks_url is None):
            raise ValueError("set exactly one of key or jwks_url")
        algs = list(algorithms)
        if not algs or any(a.lower() == "none" for a in algs):
            raise ValueError("algorithms must be non-empty and exclude 'none'")
        if jwks_url is not None and not set(algs) <= _ASYMMETRIC:
            # HS* with a JWKS would let anyone holding the public key forge tokens.
            raise ValueError("JWKS keys need asymmetric algorithms")
        self.issuer, self.audience, self.algorithms, self.leeway = issuer, audience, algs, leeway
        self._key = key
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True) if jwks_url else None

    def _signing_key(self, token: str) -> Any:
        if self._jwks is not None:
            return self._jwks.get_signing_key_from_jwt(token).key
        return self._key

    def verify(self, token: str) -> Principal | None:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._signing_key(token),
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iss", "aud", "sub", "tenant_id"]},
            )
        except jwt.PyJWTError:
            return None
        tenant = str(claims["tenant_id"])
        if not TENANT_RE.match(tenant):
            return None
        scope = claims.get("scope", "")
        scopes = scope.split() if isinstance(scope, str) else [str(s) for s in scope]
        return Principal(str(claims["sub"]), tenant, frozenset(scopes))


def verifier_from_settings(settings: Any) -> OidcVerifier | None:
    key = settings.oidc_key.get_secret_value() if settings.oidc_key else None
    if key is None and settings.oidc_jwks_url is None:
        return None
    return OidcVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        key=key,
        jwks_url=settings.oidc_jwks_url,
        algorithms=settings.oidc_algorithms,
    )


def require(scope: str) -> Callable[..., Principal]:
    """FastAPI dependency: a verified principal holding `scope`, else 401/403 (503 if unset)."""

    def dep(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
        verifier: OidcVerifier | None = request.app.state.verifier
        if verifier is None:
            raise HTTPException(503, "auth not configured: set PAP_OIDC_JWKS_URL or PAP_OIDC_KEY")
        challenge = {"WWW-Authenticate": f'Bearer scope="{scope}"'}
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(401, "bearer token required", headers=challenge)
        p = verifier.verify(authorization.removeprefix("Bearer ").strip())
        if p is None:
            raise HTTPException(401, "invalid token", headers=challenge)
        if scope not in p.scopes:
            raise HTTPException(403, f"missing scope {scope}", headers=challenge)
        return p

    return dep


Admin = Annotated[Principal, Depends(require(ADMIN))]
Ingester = Annotated[Principal, Depends(require(INGEST))]
