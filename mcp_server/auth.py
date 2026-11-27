"""OAuth 2.1 resource-server side: verify JWT access tokens, tenant comes from a claim.

The MCP server is a resource server only (MCP authorization spec): it never issues tokens.
Tokens must carry `aud` = this server's resource URL (RFC 8707), our issuer, a `tenant_id`
claim and the `pap:search` scope. The tenant is never taken from a header or argument.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from apps.api.ingest import TENANT_RE

SCOPE = "pap:search"


class JwtVerifier:
    """`mcp.server.auth.provider.TokenVerifier` backed by PyJWT."""

    def __init__(
        self,
        key: str,
        *,
        issuer: str,
        audience: str,
        algorithms: Sequence[str] = ("HS256",),
        leeway: int = 30,
    ) -> None:
        if not key:
            raise ValueError("MCP JWT key is empty")
        self._key = key
        self.issuer = issuer
        self.audience = audience
        self.algorithms = list(algorithms)
        self.leeway = leeway

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._key,
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
        scopes = scope.split() if isinstance(scope, str) else list(scope)
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or claims["sub"]),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(claims["sub"]),
            claims={"tenant_id": tenant, "iss": claims["iss"]},
        )


def current_tenant() -> str:
    """Tenant of the authenticated caller. Raises if the request is unauthenticated."""
    tok = get_access_token()
    if tok is None or not tok.claims or "tenant_id" not in tok.claims:
        raise PermissionError("no authenticated tenant")
    return str(tok.claims["tenant_id"])
