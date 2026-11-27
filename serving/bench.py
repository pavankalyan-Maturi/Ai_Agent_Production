"""Streaming benchmark for any OpenAI-compatible endpoint (vLLM direct or through LiteLLM).

    uv run python -m serving.bench --base-url http://127.0.0.1:8000/v1 --model local-llm \
        --arm prefix --concurrency 1 8 32 --requests 64 --out serving/results/prefix.json

Workload: every request shares one long system prompt (the agent's real shape: fixed
instructions + tool schemas) and a distinct short question, so prefix caching has something to
hit. Metrics per concurrency level: TTFT p50/p95, TPOT p50 (inter-token time after the first
token), aggregate output tok/s. Token counts come from `usage` when the server sends it
(`stream_options.include_usage`), else from the number of content chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

SYSTEM_PROMPT = " ".join(
    f"Rule {i}: answer only from retrieved context, cite chunk ids, refuse when unsupported."
    for i in range(120)
)  # roughly 1.8k tokens of shared prefix


@dataclass
class Sample:
    ttft_s: float
    total_s: float
    output_tokens: int


@dataclass
class LevelResult:
    concurrency: int
    requests: int
    errors: int
    ttft_p50_ms: float
    ttft_p95_ms: float
    tpot_p50_ms: float
    output_tok_per_s: float
    wall_s: float


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


async def one_request(
    client: httpx.AsyncClient, model: str, i: int, max_tokens: int, headers: dict[str, str]
) -> Sample:
    body: dict[str, Any] = {
        "model": model,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream_options": {"include_usage": True},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Question {i}: summarise rule {i % 120} in one line."},
        ],
    }
    t0 = time.perf_counter()
    ttft: float | None = None
    chunks = 0
    usage_tokens: int | None = None
    async with client.stream("POST", "/chat/completions", json=body, headers=headers) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            evt = json.loads(line[6:])
            if evt.get("usage"):
                usage_tokens = int(evt["usage"].get("completion_tokens", 0))
            for ch in evt.get("choices", []):
                if ch.get("delta", {}).get("content"):
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    chunks += 1
    total = time.perf_counter() - t0
    return Sample(ttft if ttft is not None else total, total, usage_tokens or chunks)


def summarise(concurrency: int, samples: list[Sample], errors: int, wall: float) -> LevelResult:
    tpots = [(s.total_s - s.ttft_s) / (s.output_tokens - 1) for s in samples if s.output_tokens > 1]
    return LevelResult(
        concurrency=concurrency,
        requests=len(samples),
        errors=errors,
        ttft_p50_ms=1000 * _pct([s.ttft_s for s in samples], 0.5),
        ttft_p95_ms=1000 * _pct([s.ttft_s for s in samples], 0.95),
        tpot_p50_ms=1000 * statistics.median(tpots) if tpots else float("nan"),
        output_tok_per_s=sum(s.output_tokens for s in samples) / wall if wall > 0 else 0.0,
        wall_s=wall,
    )


async def run_level(
    base_url: str, model: str, concurrency: int, n: int, max_tokens: int, api_key: str | None
) -> LevelResult:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    sem = asyncio.Semaphore(concurrency)
    samples: list[Sample] = []
    errors = 0
    limits = httpx.Limits(max_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=120, limits=limits) as client:

        async def task(i: int) -> None:
            nonlocal errors
            async with sem:
                try:
                    samples.append(await one_request(client, model, i, max_tokens, headers))
                except (httpx.HTTPError, json.JSONDecodeError):
                    errors += 1

        t0 = time.perf_counter()
        await asyncio.gather(*(task(i) for i in range(n)))
        wall = time.perf_counter() - t0
    return summarise(concurrency, samples, errors, wall)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    key = os.environ.get(args.api_key_env)
    # Warm-up request so the first level does not pay model/graph compilation.
    await run_level(args.base_url, args.model, 1, 1, args.max_tokens, key)
    levels = [
        asdict(await run_level(args.base_url, args.model, c, args.requests, args.max_tokens, key))
        for c in args.concurrency
    ]
    return {"arm": args.arm, "model": args.model, "max_tokens": args.max_tokens, "levels": levels}


def main(argv: list[str] | None = None) -> dict[str, Any]:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    p.add_argument("--model", default="local-llm")
    p.add_argument("--arm", default="baseline")
    p.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32])
    p.add_argument("--requests", type=int, default=64, help="requests per concurrency level")
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--api-key-env", default="VLLM_API_KEY")
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    result = asyncio.run(run(args))
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return result


if __name__ == "__main__":
    main()
