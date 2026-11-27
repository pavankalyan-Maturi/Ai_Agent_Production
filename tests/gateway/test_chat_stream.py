from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.chat import ChatDeps
from apps.api.main import create_app
from gateway.cache import LLMCache
from gateway.client import StreamClient
from gateway.mock_llm import app as mock_app
from gateway.ratelimit import Limit, RateLimiter
from tests.tokens import auth_settings, bearer

BODY = {"model": "mock", "messages": [{"role": "user", "content": "hello gateway world"}]}


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def client(redis_url: str, mock_llm_url: str):
    import redis.asyncio as aioredis

    def deps() -> ChatDeps:  # built per app instance; loop-bound clients made lazily
        if not hasattr(deps, "v"):
            r = aioredis.from_url(redis_url)
            deps.v = ChatDeps(  # type: ignore[attr-defined]
                RateLimiter(r, (Limit("rpm", 3), Limit("tpm", 100_000)), prefix="rl-test"),
                LLMCache(r, prefix="llm-test"),
                StreamClient(httpx.AsyncClient(base_url=mock_llm_url), "k"),
            )
        return deps.v  # type: ignore[attr-defined]

    import redis as sync_redis

    sync_redis.Redis.from_url(redis_url).flushdb()
    with TestClient(create_app(auth_settings(), chat_deps=deps)) as c:
        yield c


def test_stream_then_exact_cache_hit(client: TestClient) -> None:
    h = bearer(scope="pap:chat")
    first = _events(client.post("/v1/chat/stream", json=BODY, headers=h).text)
    assert first[0] == ("meta", {"cache": "miss", "model": "mock"})
    text = "".join(d["text"] for e, d in first if e == "delta")
    assert text.startswith("mock answer (3 words)") and first[-1][0] == "done"
    second = _events(client.post("/v1/chat/stream", json=BODY, headers=h).text)
    assert second[0][1]["cache"] == "exact" and second[1][1]["text"] == text
    other = _events(
        client.post("/v1/chat/stream", json=BODY, headers=bearer("globex", "pap:chat")).text
    )
    assert other[0][1]["cache"] == "miss"  # no cross-tenant hit


def test_auth_model_allowlist_and_429(client: TestClient) -> None:
    assert client.post("/v1/chat/stream", json=BODY).status_code == 401
    assert client.post("/v1/chat/stream", json=BODY, headers=bearer()).status_code == 403
    h = bearer(scope="pap:chat")
    bad = client.post("/v1/chat/stream", json=BODY | {"model": "gpt-x"}, headers=h)
    assert bad.status_code == 400
    codes = [client.post("/v1/chat/stream", json=BODY, headers=h).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.post("/v1/chat/stream", json=BODY, headers=h)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 20


def test_upstream_failure_is_an_sse_error(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/stream", json=BODY | {"model": "mock-flaky"}, headers=bearer(scope="pap:chat")
    )
    ev = _events(r.text)
    assert ev[-1] == ("error", {"error": "upstream unavailable"})


async def test_consumer_cancel_closes_upstream_stream(mock_llm_url: str) -> None:
    stats = mock_app.state.stats
    before = stats["streams_cancelled"]
    async with httpx.AsyncClient(base_url=mock_llm_url) as http:
        sc = StreamClient(http, "k")
        msgs = [{"role": "user", "content": "one two three four five six seven"}]

        async def consume() -> None:
            async for _ in sc.stream("mock-slow", msgs):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)  # a couple of 0.2 s chunks in
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    for _ in range(50):
        if stats["streams_cancelled"] > before:
            break
        await asyncio.sleep(0.02)
    assert stats["streams_cancelled"] == before + 1
