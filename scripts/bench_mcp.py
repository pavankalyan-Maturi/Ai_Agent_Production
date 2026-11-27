"""Per-call latency: SDK client -> MCP server, direct vs through mcp-rbac-gateway (in-process).

    uv run python scripts/bench_mcp.py -n 500

ASGI in-process transports (no sockets), fake 0-cost tool, SQLite audit on local disk. So the
numbers are protocol + auth + gateway overhead, not network or retrieval.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from agent.governance import Governance
from mcp_server.gateway import build_gateway
from tests.mcp.harness import AUD, FakeTool, client, http, running, server_app, token


def pct(xs: list[float], p: float) -> float:
    return statistics.quantiles(xs, n=100)[int(p) - 1]


async def measure(url: str, app: object, headers: dict[str, str], n: int) -> list[float]:
    out = []
    async with client(url, http(app, headers), "2026-07-28") as c:
        for i in range(n):
            t0 = time.perf_counter()
            r = await c.call_tool("search", {"query": f"q{i}"})
            out.append((time.perf_counter() - t0) * 1000)
            assert not r.is_error
    return out


async def main(n: int) -> None:
    secret = "bench-only"  # noqa: S105 - local benchmark, not a credential
    os.environ["PAP_MCP_RESEARCHER_TOKEN"] = secret
    upstream = server_app(FakeTool("search"), FakeTool("fetch_doc"))
    with tempfile.TemporaryDirectory() as d:
        gov = Governance.from_yaml(audit_path=str(Path(d) / "audit.db"))
        gw = build_gateway(
            gov.sentinel, AUD, approval_token=secret, transport=httpx.ASGITransport(app=upstream)
        )
        auth = {"Authorization": f"Bearer {token()}"}
        async with running(upstream):
            await measure(AUD, upstream, auth, 20)  # warm up
            direct = await measure(AUD, upstream, auth, n)
            via = await measure("http://gw.test/mcp", gw, {**auth, "X-Sentinel-Token": secret}, n)
        gov.close()
    for name, xs in (("direct", direct), ("gateway", via)):
        print(f"{name:8s} n={n} p50={statistics.median(xs):.2f} ms p95={pct(xs, 95):.2f} ms")
    print(f"gateway overhead p50 = {statistics.median(via) - statistics.median(direct):.2f} ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=500)
    asyncio.run(main(ap.parse_args().n))
