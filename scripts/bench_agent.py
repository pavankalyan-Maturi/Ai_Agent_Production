"""Phase 2A numbers: checkpoint overhead per step, and kill/resume over repeated trials.

usage: PAP_TEST_DATABASE_URL=... uv run python scripts/bench_agent.py [-n 50] [--kills 10]
Scripted model, no network: measures the graph + checkpointer, not an LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph
from agent.model import ScriptedModel
from agent.runner import DURABILITY, postgres_checkpointer
from agent.state import Budget
from agent.tools import calculator
from tests.agent import crash_worker
from tests.agent.scripted import plan_policy

TOOL_STEPS = 10
STEPS = [("calculator", {"expression": f"{i}*2"}) for i in range(TOOL_STEPS)] + ["{last}"]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


async def timed_runs(saver: Any, n: int) -> list[float]:
    out = []
    for _ in range(n):
        g = build_graph(
            ScriptedModel(plan_policy(STEPS)),
            [calculator()],
            Budget(max_steps=50),
            checkpointer=saver,
        )
        t0 = time.perf_counter()
        s = await g.ainvoke(
            {"tenant_id": "bench", "question": "q"},
            {"configurable": {"thread_id": f"bench:{uuid.uuid4()}"}},
            durability=DURABILITY,
        )
        out.append(time.perf_counter() - t0)
        assert s["answer"] == "18.0"
    return out


def kill_resume(dsn: str) -> float:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as d:
        log, flag = Path(d) / "log", Path(d) / "hang"
        flag.touch()
        thread = f"acme:{uuid.uuid4()}"
        p = subprocess.Popen(  # noqa: S603 - fixed argv, our own module
            [sys.executable, "-m", "tests.agent.crash_worker", dsn, thread, str(log), str(flag)],
            cwd=root,
        )
        while not (log.exists() and "hang" in log.read_text()):
            time.sleep(0.02)
        p.send_signal(signal.SIGKILL)
        p.wait()
        flag.unlink()

        async def resume() -> float:
            async with postgres_checkpointer(dsn) as saver:
                g = crash_worker.graph(saver, str(log), str(flag))
                t0 = time.perf_counter()
                s = await g.ainvoke(
                    None, {"configurable": {"thread_id": thread}}, durability=DURABILITY
                )
                assert s["answer"] == "done after work 3 ok", s
                return time.perf_counter() - t0

        return asyncio.run(resume())


async def main(n: int, kills: int) -> None:
    dsn = os.environ["PAP_TEST_DATABASE_URL"]
    await timed_runs(InMemorySaver(), 3)  # warm-up
    mem = await timed_runs(InMemorySaver(), n)
    async with postgres_checkpointer(dsn) as saver:
        await timed_runs(saver, 3)
        pg = await timed_runs(saver, n)
    loop = asyncio.get_running_loop()
    resumes = [await loop.run_in_executor(None, kill_resume, dsn) for _ in range(kills)]
    graph_steps = 2 * TOOL_STEPS + 2  # plan+act per tool step, start, final plan
    print(
        json.dumps(
            {
                "runs": n,
                "tool_steps_per_run": TOOL_STEPS,
                "supersteps_per_run": graph_steps,
                "memory_ms_p50": round(1e3 * statistics.median(mem), 2),
                "postgres_ms_p50": round(1e3 * statistics.median(pg), 2),
                "postgres_ms_p95": round(1e3 * pct(pg, 0.95), 2),
                "checkpoint_overhead_ms_per_superstep_p50": round(
                    1e3 * (statistics.median(pg) - statistics.median(mem)) / graph_steps, 3
                ),
                "kill_resume_trials": kills,
                "kill_resume_success": len(resumes),
                "resume_ms_p50": round(1e3 * statistics.median(resumes), 2),
                "resume_ms_max": round(1e3 * max(resumes), 2),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=50)
    ap.add_argument("--kills", type=int, default=10)
    a = ap.parse_args()
    asyncio.run(main(a.n, a.kills))
