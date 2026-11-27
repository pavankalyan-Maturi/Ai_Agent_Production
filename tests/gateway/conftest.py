"""Real Redis and a real (uvicorn) mock upstream for gateway tests.

Redis: PAP_TEST_REDIS_URL (CI service) or a throwaway `redis-server` on a free port. In CI a
missing Redis is a failure, not a skip.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import redis
import redis.asyncio as aioredis
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    if url := os.environ.get("PAP_TEST_REDIS_URL"):
        yield url
        return
    if not shutil.which("redis-server"):
        if os.environ.get("CI"):
            pytest.fail("CI needs Redis: set PAP_TEST_REDIS_URL")
        pytest.skip("no redis-server and no PAP_TEST_REDIS_URL")
    port = _free_port()
    proc = subprocess.Popen(  # noqa: S603  # fixed argv, test-only
        ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],  # noqa: S607
        stdout=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    for _ in range(50):
        try:
            redis.Redis.from_url(url).ping()
            break
        except redis.ConnectionError:
            time.sleep(0.05)
    yield url
    proc.terminate()
    proc.wait(5)


@pytest.fixture
async def aredis(redis_url: str) -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(redis_url)
    await r.flushdb()
    yield r
    await r.aclose()


@pytest.fixture(scope="session")
def mock_llm_url() -> Iterator[str]:
    from gateway.mock_llm import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)
