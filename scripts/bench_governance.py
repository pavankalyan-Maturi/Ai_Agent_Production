"""Per-call overhead of the mcp-rbac-gateway pipeline (policy, scans, audit write).

usage: uv run python scripts/bench_governance.py [-n 2000]
Tool = calculator (microseconds), so the numbers are the gateway's cost, not the tool's.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.governance import Governance
from agent.tools import calculator

DOC = "[d1] " + "Paris is the capital and most populous city of France. " * 40  # ~2 kB result


def timed(fn: object, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()  # type: ignore[operator]
        out.append((time.perf_counter() - t0) * 1e6)
    return out


def pct(xs: list[float], q: float) -> float:
    return sorted(xs)[min(len(xs) - 1, int(q * len(xs)))]


def main(n: int) -> None:
    calc = calculator()
    with tempfile.TemporaryDirectory() as d:
        gov = Governance.from_yaml(audit_path=str(Path(d) / "audit.db"))
        args = {"expression": "12*3.5"}
        raw = timed(lambda: calc.fn("bench", args), n)
        gated = timed(
            lambda: gov.execute(calc, tenant_id="bench", role="researcher", args=args, call_id="c"),
            n,
        )
        denied = timed(
            lambda: gov.execute(calc, tenant_id="bench", role="anonymous", args=args, call_id="c"),
            n,
        )
        scan = timed(lambda: gov.sentinel.check_result(DOC), n)
        rows = gov.sentinel.audit.count() if gov.sentinel.audit else 0
        gov.close()
    print(
        json.dumps(
            {
                "calls": n,
                "raw_tool_us_p50": round(statistics.median(raw), 1),
                "governed_us_p50": round(statistics.median(gated), 1),
                "governed_us_p95": round(pct(gated, 0.95), 1),
                "denied_us_p50": round(statistics.median(denied), 1),
                "output_scan_2kB_us_p50": round(statistics.median(scan), 1),
                "audit_rows_written": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=2000)
    main(ap.parse_args().n)
