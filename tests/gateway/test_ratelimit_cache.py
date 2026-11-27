from __future__ import annotations

import math

from gateway.cache import LLMCache, salient, semantic_query
from gateway.ratelimit import Limit, RateLimiter
from retrieval.embedding import HashEmbedder

U = [{"role": "user", "content": "What was ACME revenue in 2023?"}]
P = {"max_tokens": 64}


async def test_bucket_refuses_after_capacity_and_reports_retry(aredis) -> None:
    rl = RateLimiter(aredis, (Limit("rpm", 3),))
    got = [(await rl.take("acme", "u1", "rpm")).allowed for _ in range(4)]
    assert got == [True, True, True, False]
    v = await rl.take("acme", "u1", "rpm")
    assert not v.allowed and 0 < v.retry_after_s <= 20.0 and v.limit == "rpm"


async def test_buckets_are_per_tenant_and_per_caller(aredis) -> None:
    rl = RateLimiter(aredis, (Limit("rpm", 1),))
    assert (await rl.take("acme", "u1", "rpm")).allowed
    assert not (await rl.take("acme", "u1", "rpm")).allowed
    assert (await rl.take("acme", "u2", "rpm")).allowed
    assert (await rl.take("globex", "u1", "rpm")).allowed


async def test_cost_above_capacity_never_fits(aredis) -> None:
    rl = RateLimiter(aredis, (Limit("rpm", 10), Limit("tpm", 100)))
    v = await rl.check("acme", "u1", {"rpm": 1, "tpm": 101})
    assert not v.allowed and math.isinf(v.retry_after_s) and v.limit == "tpm"


async def test_exact_hit_is_tenant_scoped(aredis) -> None:
    c = LLMCache(aredis)
    await c.put("acme", "mock", U, P, "42M", 10, 3)
    hit = await c.get("acme", "mock", U, P)
    assert hit and hit.kind == "exact" and hit.text == "42M" and hit.output_tokens == 3
    assert await c.get("globex", "mock", U, P) is None
    assert await c.get("acme", "agent-fast", U, P) is None
    assert await c.get("acme", "mock", U, {"max_tokens": 65}) is None


async def test_semantic_hit_and_salient_guard(aredis) -> None:
    c = LLMCache(aredis, HashEmbedder(), threshold=0.6)
    await c.put("acme", "mock", U, P, "42M")
    para = [{"role": "user", "content": "ACME revenue in 2023, what was it"}]
    hit = await c.get("acme", "mock", para, P)
    assert hit and hit.kind == "semantic" and hit.text == "42M"
    other_year = [{"role": "user", "content": "What was ACME revenue in 2024?"}]
    assert await c.get("acme", "mock", other_year, P) is None  # guard: 2023 != 2024
    unguarded = LLMCache(aredis, HashEmbedder(), threshold=0.6, guard=False)
    assert (await unguarded.get("acme", "mock", other_year, P)) is not None  # the false hit
    assert await c.get("globex", "mock", para, P) is None


def test_only_single_turn_prompts_are_semantic() -> None:
    assert semantic_query(U, P) == U[0]["content"]
    assert semantic_query(U, P | {"temperature": 0.7}) is None
    turn2 = [*U, {"role": "assistant", "content": "42M"}, {"role": "user", "content": "and 2024?"}]
    assert semantic_query(turn2, P) is None
    assert salient('is "Q3" not 1,200 units') == ("1,200", "not", "q3")


async def test_guard_removes_number_and_negation_false_hits(aredis) -> None:
    from gateway.cache_pairs import pairs

    async def false_hits(guard: bool) -> set[str]:
        c = LLMCache(aredis, HashEmbedder(), threshold=0.9, guard=guard)
        kinds = set()
        for i, p in enumerate(x for x in pairs() if not x.same):
            await c.put(f"t{i}", "m", [{"role": "user", "content": p.a}], P, "A")
            if await c.get(f"t{i}", "m", [{"role": "user", "content": p.b}], P):
                kinds.add(p.kind)
        return kinds

    assert "negation" in await false_hits(guard=False)  # the risk is real at 0.9
    assert await false_hits(guard=True) == set()
