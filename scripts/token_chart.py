"""P2.4 token chart: estimated context size per model call, compaction off vs on.

Scripted model, 12 search steps of ~6 kB each (no network, deterministic). Prints a Mermaid
xychart for RESULTS.md and the raw series.  usage: uv run python scripts/token_chart.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.compaction import CompactionPolicy
from agent.graph import build_graph
from agent.model import ScriptedModel, ToolSpec
from agent.state import Budget
from agent.tools import Tool
from tests.agent.scripted import plan_policy

STEPS: list[Any] = [("search", {"i": i}) for i in range(12)] + ["done"]


def search() -> Tool:
    return Tool(ToolSpec("search", "", {}), lambda _t, a: f"[d{a['i']}] " + "evidence " * 650)


def series(policy: CompactionPolicy | None) -> list[int]:
    g = build_graph(
        ScriptedModel(plan_policy(STEPS)), [search()], Budget(max_steps=30), compaction=policy
    )
    return list(g.invoke({"tenant_id": "chart", "question": "q"})["context_tokens"])


def main() -> None:
    off = series(None)
    on = series(CompactionPolicy(tool_result_chars=3000, threshold_tokens=5000, keep_last=4))
    xs = ", ".join(str(i + 1) for i in range(len(off)))
    print("```mermaid")
    print("xychart-beta")
    print('    title "Estimated context tokens per model call (scripted, 12 x 6 kB tool results)"')
    print(f'    x-axis "model call" [{xs}]')
    print(f'    y-axis "tokens (est.)" 0 --> {max(off) // 1000 * 1000 + 1000}')
    print(f"    line [{', '.join(map(str, off))}]")
    print(f"    line [{', '.join(map(str, on))}]")
    print("```")
    print(f"off: {off}\non:  {on}")
    print(f"peak off {max(off)}, peak on {max(on)}, total off {sum(off)}, total on {sum(on)}")


if __name__ == "__main__":
    main()
