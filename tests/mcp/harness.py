"""In-process MCP server + gateway wiring for tests (no sockets)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
import httpx2
import jwt
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from agent.model import ToolSpec
from agent.tools import Tool
from mcp_server.auth import JwtVerifier
from mcp_server.server import build_app, build_server

KEY = "test-key-" + "x" * 32
ISS = "https://idp.test"
AUD = "http://mcp.test/mcp"


def token(tenant: str = "acme", key: str = KEY, **over: Any) -> str:
    claims: dict[str, Any] = {
        "sub": "user-1",
        "iss": ISS,
        "aud": AUD,
        "exp": int(time.time()) + 60,
        "tenant_id": tenant,
        "scope": "pap:search",
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, "HS256")


class FakeTool:
    def __init__(self, name: str, out: Callable[[str, dict[str, Any]], str] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.out = out or (lambda t, a: f"[{t}-d1] {name} result for {a}")

        def fn(tenant: str, args: dict[str, Any]) -> str:
            self.calls.append((tenant, args))
            return self.out(tenant, args)

        self.tool = Tool(ToolSpec(name, f"{name} over this tenant's docs", {}), fn)


def server_app(search: FakeTool, fetch: FakeTool) -> Starlette:
    verifier = JwtVerifier(KEY, issuer=ISS, audience=AUD)
    return build_app(build_server(search.tool, fetch.tool, verifier, issuer_url=ISS), ["mcp.test"])


class Recording(httpx.AsyncBaseTransport):
    """httpx transport into an ASGI app that keeps every forwarded request."""

    def __init__(self, app: Any) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


class RoundRobin(httpx2.AsyncBaseTransport):
    """A load balancer with no affinity: request i goes to replica i % n."""

    def __init__(self, apps: list[Any]) -> None:
        self.replicas = [httpx2.ASGITransport(app=a) for a in apps]
        self.hits = [0] * len(apps)
        self._i = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        i, self._i = self._i % len(self.replicas), self._i + 1
        self.hits[i] += 1
        return await self.replicas[i].handle_async_request(request)


@asynccontextmanager
async def running(*apps: Starlette) -> AsyncIterator[None]:
    """Run the apps' lifespans (the MCP session manager needs its task group)."""
    async with AsyncExitStack() as stack:
        for a in apps:
            await stack.enter_async_context(a.router.lifespan_context(a))
        yield


def http(app_or_transport: Any, headers: dict[str, str]) -> httpx2.AsyncClient:
    transport = (
        app_or_transport
        if isinstance(app_or_transport, httpx2.AsyncBaseTransport)
        else httpx2.ASGITransport(app=app_or_transport)
    )
    return httpx2.AsyncClient(transport=transport, headers=headers)


def client(url: str, hc: httpx2.AsyncClient, mode: str = "auto") -> Client:
    return Client(streamable_http_client(url, http_client=hc), mode=mode)  # type: ignore[arg-type]
