"""Gateway numbers for RESULTS.md: cache false hits, hit rate/savings, lookup and API latency.

`uv run python scripts/bench_gateway.py --redis redis://127.0.0.1:6379/15 -n 1000`
Needs a Redis it may FLUSH (use a spare db index). Embedder: hash-bow (lexical, offline);
`--fastembed` uses bge-small if installed. Workload is synthetic (stated in output).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from typing import Any

import redis.asyncio as aioredis

from agent.model import PRICES
from gateway.cache import LLMCache
from gateway.cache_pairs import pairs
from retrieval.embedding import HashEmbedder

P = {"max_tokens": 256}


def _msg(q: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": q}]


def _pct(xs: list[float], q: float) -> float:
    return statistics.quantiles(xs, n=100)[int(q) - 1] if len(xs) > 1 else xs[0]


async def false_hits(r: Any, emb: Any, threshold: float) -> None:
    print(f"\n## Semantic false hits ({emb.name}, threshold {threshold})")
    for guard in (False, True):
        c = LLMCache(r, emb, threshold=threshold, guard=guard, prefix="fh")
        tp = fp = n_same = n_diff = 0
        by_kind: dict[str, int] = {}
        for i, p in enumerate(pairs()):
            await r.flushdb()
            await c.put(f"t{i}", "m", _msg(p.a), P, "A")
            hit = await c.get(f"t{i}", "m", _msg(p.b), P)
            if p.same:
                n_same += 1
                tp += hit is not None
            else:
                n_diff += 1
                fp += hit is not None
                by_kind[p.kind] = by_kind.get(p.kind, 0) + (hit is not None)
        print(f"guard={guard}: paraphrase hits {tp}/{n_same}, false hits {fp}/{n_diff} {by_kind}")


async def workload(r: Any, emb: Any, n: int, threshold: float) -> None:
    rnd = random.Random(7)  # noqa: S311  # seeded workload, not crypto
    base = [p.a for p in pairs() if p.kind == "reorder"]
    rewrites = {a: [p.b for p in pairs() if p.same and p.a == a] for a in base}
    pool = [f"Summarise document {k} for tenant review" for k in range(195)] + base
    weights = [1 / (i + 1) ** 1.1 for i in range(len(pool))]  # Zipf-ish popularity
    rnd.shuffle(weights)
    await r.flushdb()
    c = LLMCache(r, emb, threshold=threshold, prefix="wl")
    counts = {"exact": 0, "semantic": 0, "miss": 0}
    lat: dict[str, list[float]] = {"exact": [], "semantic": [], "miss": []}
    pin, pout = PRICES["claude-haiku-5-5"]
    for _ in range(n):
        q = rnd.choices(pool, weights)[0]
        if q in rewrites and rnd.random() < 0.5:
            q = rnd.choice(rewrites[q])
        t0 = time.perf_counter()
        hit = await c.get("acme", "agent-fast", _msg(q), P)
        kind = hit.kind if hit else "miss"
        lat[kind].append((time.perf_counter() - t0) * 1000)
        counts[kind] += 1
        if hit is None:
            await c.put("acme", "agent-fast", _msg(q), P, "answer " * 50, 800, 200)
    saved = (counts["exact"] + counts["semantic"]) * (800 * pin + 200 * pout) / 1e6
    print(f"\n## Synthetic workload: n={n}, 200 prompts, Zipf(1.1), 50% rewording on 5 of them")
    print(f"hits exact {counts['exact']} semantic {counts['semantic']} miss {counts['miss']}")
    print(
        f"hit rate {(n - counts['miss']) / n:.1%}; Haiku cost avoided ${saved:.4f} at 800/200 tok"
    )
    for k, xs in lat.items():
        if xs:
            print(f"lookup {k}: p50 {_pct(xs, 50):.3f} ms p95 {_pct(xs, 95):.3f} ms (n={len(xs)})")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redis", default="redis://127.0.0.1:6379/15")
    ap.add_argument("-n", type=int, default=1000)
    ap.add_argument("--threshold", type=float, default=0.9)
    ap.add_argument("--fastembed", action="store_true")
    a = ap.parse_args()
    r = aioredis.from_url(a.redis)
    emb: Any = HashEmbedder()
    if a.fastembed:
        from retrieval.embedding import FastEmbedEmbedder

        emb = FastEmbedEmbedder()
    await false_hits(r, emb, a.threshold)
    await workload(r, emb, a.n, a.threshold)
    await r.flushdb()
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
