"""Subprocess for the crash-resume test: runs a 5-step agent that hangs inside step 3's tool.

usage: python -m tests.agent.crash_worker DSN THREAD_ID LOG HANG_FLAG
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

from agent.graph import build_graph
from agent.model import ScriptedModel, ToolSpec
from agent.runner import postgres_checkpointer
from agent.tools import Tool
from tests.agent.scripted import plan_policy

STEPS: list[tuple[str, dict[str, Any]] | str] = [("work", {"i": i}) for i in range(4)] + [
    "done after {last}"
]


def work_tool(log: str, hang_flag: str) -> Tool:
    def fn(tenant: str, args: dict[str, Any]) -> str:
        with open(log, "a") as f:
            f.write(f"{tenant} {args['i']}\n")
        if args["i"] == 2 and os.path.exists(hang_flag):
            with open(log, "a") as f:
                f.write("hang\n")
            time.sleep(3600)
        return f"work {args['i']} ok"

    return Tool(ToolSpec("work", "do work", {"type": "object", "properties": {}}), fn)


def graph(saver: Any, log: str, hang_flag: str) -> Any:
    return build_graph(
        ScriptedModel(plan_policy(STEPS)), [work_tool(log, hang_flag)], checkpointer=saver
    )


async def main(dsn: str, thread: str, log: str, hang_flag: str) -> None:
    async with postgres_checkpointer(dsn) as saver:
        await graph(saver, log, hang_flag).ainvoke(
            {"tenant_id": "acme", "question": "q"}, {"configurable": {"thread_id": thread}}
        )


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:5]))
