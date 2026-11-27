"""Process entry points. Run with uvicorn's factory mode:

    uvicorn --factory mcp_server.app:server_app  --port 8001   # MCP server (internal only)
    uvicorn --factory mcp_server.app:gateway_app --port 8002   # gateway (public)

Only the gateway should be reachable by agents; the server trusts it to enforce roles.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.applications import Starlette

from agent.governance import Governance
from agent.tools import fetch_doc_tool, search_tool
from apps.api.settings import Settings, get_settings
from mcp_server.auth import JwtVerifier
from mcp_server.gateway import build_gateway
from mcp_server.server import build_app, build_server
from retrieval.embedding import Embedder, FastEmbedEmbedder, HashEmbedder
from retrieval.store import PgStore


def server_app(settings: Settings | None = None) -> Starlette:
    s = settings or get_settings()
    if s.mcp_jwt_key is None:
        raise RuntimeError("PAP_MCP_JWT_KEY is required to serve MCP")
    embedder: Embedder = (
        HashEmbedder(s.embedding_dim) if s.embedder == "hash" else FastEmbedEmbedder()
    )
    store = PgStore(str(s.database_url), embedder.dim)
    verifier = JwtVerifier(
        s.mcp_jwt_key.get_secret_value(),
        issuer=s.mcp_issuer,
        audience=s.mcp_resource_url,
        algorithms=s.mcp_jwt_algorithms,
    )
    mcp = build_server(
        search_tool(store, embedder), fetch_doc_tool(store), verifier, issuer_url=s.mcp_issuer
    )
    return build_app(mcp, s.mcp_allowed_hosts)


def gateway_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    gov = Governance.from_yaml(s.sentinel_config, audit_path=s.sentinel_audit_db)
    if s.mcp_approval_token is None:
        raise RuntimeError("PAP_MCP_APPROVAL_TOKEN is required to serve the gateway")
    return build_gateway(
        gov.sentinel, s.mcp_upstream_url, approval_token=s.mcp_approval_token.get_secret_value()
    )
