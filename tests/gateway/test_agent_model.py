"""GatewayModel: the agent graph's model node behind the gateway cache + limiter (real Redis)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
import redis

from agent.graph import build_graph
from agent.model import Decision, ScriptedModel, ToolCall, ToolSpec
from agent.state import Budget
from agent.tools import Tool
from gateway.agent_model import GatewayModel, RateLimited
from gateway.ratelimit import Limit, RateLimiter, SyncRateLimiter
from tests.agent.scripted import plan_policy

M = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
T = [ToolSpec("search", "find", {"type": "object"})]


@pytest.fixture
def r(redis_url: str) -> Iterator[redis.Redis]:
    c = redis.Redis.from_url(redis_url)
    c.flushdb()
    yield c
    c.close()


def scripted(d: Decision) -> ScriptedModel:
    return ScriptedModel(lambda _m: d)


def test_exact_hit_replays_tool_calls_and_spends_nothing(r: redis.Redis) -> None:
    d = Decision("", (ToolCall("c1", "search", {"q": "x"}),), 50, 7, raw=[{"type": "tool_use"}])
    inner = scripted(d)
    gm = GatewayModel(inner, r, "acme", "u1")
    first, second = gm.decide(M, T), gm.decide(M, T)
    assert inner.calls == 1 and (gm.hits, gm.misses) == (1, 1)
    assert first.input_tokens == 50 and (second.input_tokens, second.output_tokens) == (0, 0)
    assert second.tool_calls == d.tool_calls and second.raw == d.raw


def test_cache_is_tenant_and_tool_scoped(r: redis.Redis) -> None:
    inner = scripted(Decision("a"))
    GatewayModel(inner, r, "acme", "u1").decide(M, T)
    GatewayModel(inner, r, "globex", "u1").decide(M, T)
    GatewayModel(inner, r, "acme", "u1").decide(M, [])
    assert inner.calls == 3
    assert GatewayModel(inner, r, "acme", "u1").key(M, T).startswith("llm:agent:acme:")


def test_bad_tenant_rejected(r: redis.Redis) -> None:
    with pytest.raises(ValueError):
        GatewayModel(scripted(Decision("a")), r, "acme:*", "u1")


def test_limiter_refuses_and_shares_buckets_with_async_route(r: redis.Redis) -> None:
    lim = SyncRateLimiter(r, (Limit("rpm", 2), Limit("tpm", 10_000)))
    inner = scripted(Decision("a"))
    gm = GatewayModel(inner, r, "acme", "u1", limiter=lim, cache=False)
    gm.decide(M, T)
    # The streaming route's async limiter drains the same bucket key.
    assert lim.key("acme", "u1", "rpm") == RateLimiter(r, ()).key("acme", "u1", "rpm")
    gm.decide(M, T)
    with pytest.raises(RateLimited) as e:
        gm.decide(M, T)
    assert e.value.verdict.limit == "rpm" and inner.calls == 2
    other = GatewayModel(inner, r, "globex", "u1", limiter=lim, cache=False)
    other.decide(M, T)  # other tenant has its own bucket


def test_tpm_cost_above_capacity_never_waits(r: redis.Redis) -> None:
    slept: list[float] = []
    lim = SyncRateLimiter(r, (Limit("rpm", 100), Limit("tpm", 500)))
    gm = GatewayModel(
        scripted(Decision("a")), r, "acme", "u1", limiter=lim, max_wait_s=60, sleep=slept.append
    )
    with pytest.raises(RateLimited):
        gm.decide(M, T)  # 1024 max output tokens > 500 capacity
    assert slept == []


def test_limiter_waits_within_budget(r: redis.Redis) -> None:
    lim = SyncRateLimiter(r, (Limit("rpm", 600),))  # refills one request per 0.1 s
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        time.sleep(s)

    gm = GatewayModel(
        scripted(Decision("a")),
        r,
        "acme",
        "u1",
        limiter=lim,
        cache=False,
        max_wait_s=2,
        sleep=sleep,
    )
    lim.take("acme", "u1", "rpm", 600)  # drain
    gm.decide(M, T)
    assert slept and all(0 < s <= 0.2 for s in slept)


def test_second_identical_agent_run_is_served_from_cache(r: redis.Redis) -> None:
    def tool() -> Tool:
        return Tool(ToolSpec("search", "", {}), lambda _t, a: f"[d{a['i']}] fact")

    steps: list[Any] = [("search", {"i": 1}), "answer {last}"]
    inner = ScriptedModel(plan_policy(steps))
    outs = []
    for i in range(2):
        gm = GatewayModel(inner, r, "acme", "u1")
        g = build_graph(gm, [tool()], Budget(max_steps=5))
        cfg = {"configurable": {"thread_id": f"t:{i}"}}
        outs.append(asyncio.run(g.ainvoke({"tenant_id": "acme", "question": "q"}, cfg)))
    assert outs[0]["answer"] == outs[1]["answer"]
    assert inner.calls == 2  # run 1: two model calls; run 2: zero
