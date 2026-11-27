"""Stateless MCP server: tenant-scoped retrieval tools (P3.1, ADR 007).

Stateless by construction: no `Mcp-Session-Id`, no per-process state between requests, so
replicas behind a round-robin balancer need no session store. Every tool call runs as the
tenant in the caller's access token. Role-based governance (which agent may call which
tool, argument rules, redaction, injection scan, audit) is enforced in front of this
server by mcp-rbac-gateway's StatelessHttpProxy (`mcp_server.gateway`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette

from agent.tools import Tool
from mcp_server.auth import SCOPE, JwtVerifier, current_tenant

ToolFn = Callable[[str, dict[str, Any]], str]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


def build_server(
    search: Tool, fetch_doc: Tool, verifier: JwtVerifier, *, issuer_url: str
) -> MCPServer:
    mcp = MCPServer(
        "pap-retrieval",
        version="0.3.0",
        instructions="Search and read this tenant's documents. Cite [doc_id].",
        token_verifier=verifier,
        # Strings, not AnyHttpUrl(...): AuthSettings keeps a path-less issuer exact (no
        # trailing slash), which RFC 8414 issuer comparison and the JWT `iss` check need.
        auth=AuthSettings.model_validate(
            {
                "issuer_url": issuer_url,
                "resource_server_url": verifier.audience,
                "required_scopes": [SCOPE],
            }
        ),
    )

    async def run(tool: Tool, args: dict[str, Any]) -> str:
        return await asyncio.to_thread(tool.fn, current_tenant(), args)

    @mcp.tool(name="search", description=search.spec.description, annotations=READ_ONLY)
    async def search_docs(query: str) -> str:
        return await run(search, {"query": query})

    @mcp.tool(name="fetch_doc", description=fetch_doc.spec.description, annotations=READ_ONLY)
    async def fetch(doc_id: str) -> str:
        return await run(fetch_doc, {"doc_id": doc_id})

    return mcp


def build_app(mcp: MCPServer, allowed_hosts: list[str]) -> Starlette:
    """ASGI app: POST /mcp, JSON responses, no session tracking, Host header allowlist."""
    return mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )
