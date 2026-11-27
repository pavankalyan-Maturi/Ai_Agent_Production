"""mcp-rbac-gateway's StatelessHttpProxy in front of our MCP server (ADR 007).

Client --POST /mcp--> gateway (role from X-Sentinel-Token, policy from sentinel.yaml,
header/body consistency, argument rules, redaction, injection scan, audit) --> MCP server
(tenant from the OAuth access token). `Authorization` and `params._meta` (protocol
version, client info, `traceparent`) are forwarded unchanged; only `arguments` may be
rewritten by redaction. The gateway itself is stateless too, so it scales the same way.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from mcp_rbac_gateway import Sentinel, StatelessHttpProxy


def build_gateway(
    sentinel: Sentinel,
    upstream_url: str,
    *,
    approval_token: str,
    transport: Any = None,
) -> FastAPI:
    """`transport` is an httpx transport override (tests route to the in-process server).

    The gateway mounts the library's /approvals router; with no token that router would be
    open, so a token is required (fail closed).
    """
    if not approval_token:
        raise ValueError("an approval token is required for the gateway's /approvals routes")
    proxy = StatelessHttpProxy(
        sentinel,
        upstream_url,
        default_agent=sentinel.config.default_agent,
        approval_token=approval_token,
        transport=transport,
    )
    return proxy.build_app()
