"""Real retrieval behind the MCP server and gateway. Needs Postgres (PAP_TEST_DATABASE_URL)."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import httpx
import pytest
from pydantic import PostgresDsn, SecretStr

from apps.api.settings import Settings
from mcp_server.app import gateway_app, server_app
from retrieval.embedding import HashEmbedder
from retrieval.pipeline import index_documents
from retrieval.store import PgStore
from retrieval.types import Document
from tests.mcp.harness import AUD, ISS, KEY, client, http, running, token

DSN = os.environ.get("PAP_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="PAP_TEST_DATABASE_URL not set"),
]


def test_search_through_gateway_is_tenant_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert DSN
    monkeypatch.setenv("PAP_MCP_RESEARCHER_TOKEN", "r-secret")
    store = PgStore(DSN, dim=384)
    store.migrate()
    a, b = f"ma-{uuid.uuid4().hex[:8]}", f"mb-{uuid.uuid4().hex[:8]}"
    emb = HashEmbedder(384)
    index_documents(store, emb, [Document(a, "d1", "The reactor core runs at 300 C.")])
    index_documents(store, emb, [Document(b, "d9", "Secret reactor notes for B.")])
    s = Settings(
        database_url=PostgresDsn(DSN),
        embedder="hash",
        mcp_jwt_key=SecretStr(KEY),
        mcp_issuer=ISS,
        mcp_resource_url=AUD,
        mcp_allowed_hosts=["mcp.test"],
        mcp_upstream_url=AUD,
        mcp_approval_token=SecretStr("appr"),
        sentinel_audit_db=str(tmp_path / "audit.db"),
    )
    upstream = server_app(s)
    gw = gateway_app(s)
    # Point the gateway's upstream client at the in-process server.
    proxy_transport = httpx.ASGITransport(app=upstream)
    monkeypatch.setattr(httpx, "AsyncClient", _bound(httpx.AsyncClient, proxy_transport))

    async def ask(tenant: str, q: str) -> str:
        hdr = {"Authorization": f"Bearer {token(tenant)}", "X-Sentinel-Token": "r-secret"}
        async with client("http://gw.test/mcp", http(gw, hdr), "2026-07-28") as c:
            r = await c.call_tool("search", {"query": q})
            assert not r.is_error
            return r.content[0].text  # type: ignore[union-attr]

    async def go() -> list[str]:
        async with running(upstream):
            return [
                await ask(a, "reactor core"),
                await ask(a, "secret reactor notes"),
                await ask(b, "secret reactor notes"),
            ]

    try:
        own, other, b_own = asyncio.run(go())
        assert "[d1]" in own and "d9" not in other and "[d9]" in b_own
    finally:
        store.delete_tenant(a)
        store.delete_tenant(b)


def _bound(cls: type[httpx.AsyncClient], transport: httpx.AsyncBaseTransport) -> type:
    class Bound(cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kw: object) -> None:
            kw["transport"] = transport
            super().__init__(*args, **kw)  # type: ignore[arg-type]

    return Bound
