"""Sequential latency of the probe endpoints against a running stack. Prints p50/p95/p99 in ms."""

import argparse
import statistics
import time

import httpx2 as httpx


def bench(client: httpx.Client, path: str, n: int) -> dict[str, float]:
    for _ in range(20):  # warm-up
        client.get(path)
    samples = []
    for _ in range(n):
        t = time.perf_counter()
        r = client.get(path)
        samples.append((time.perf_counter() - t) * 1000)
        r.raise_for_status()
    q = statistics.quantiles(samples, n=100)
    return {"p50": q[49], "p95": q[94], "p99": q[98]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("-n", type=int, default=1000)
    a = ap.parse_args()
    with httpx.Client(base_url=a.base, timeout=5) as c:
        for path in ("/healthz", "/readyz", "/v1/ping"):
            r = bench(c, path, a.n)
            print(f"{path:10s} n={a.n} " + " ".join(f"{k}={v:.2f}ms" for k, v in r.items()))
