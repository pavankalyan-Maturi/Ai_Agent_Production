from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.model import ScriptedModel, ToolSpec, _to_anthropic, cost_usd
from agent.state import Budget
from agent.tools import Tool, calculator, safe_eval
from tests.agent.scripted import loop_policy, plan_policy


def run(graph: Any, inp: Any, thread: str = "t:1") -> dict[str, Any]:
    return asyncio.run(graph.ainvoke(inp, {"configurable": {"thread_id": thread}}))


def recorder(
    name: str, flagged: bool = False, out: str = "ok"
) -> tuple[Tool, list[tuple[str, dict]]]:
    seen: list[tuple[str, dict]] = []

    def fn(tenant: str, args: dict[str, Any]) -> str:
        seen.append((tenant, args))
        return out

    spec = ToolSpec(name, name, {"type": "object", "properties": {}})
    return Tool(spec, fn, requires_approval=flagged), seen


def test_safe_eval() -> None:
    assert safe_eval("2 * (3 + 4) ** 2 - 1 / 4") == 97.75
    assert safe_eval("-3 % 2") == 1.0
    for bad in ["__import__('os')", "x + 1", "(1).real", "2 ** 1000", "[1]", "1" * 201]:
        with pytest.raises((ValueError, SyntaxError)):
            safe_eval(bad)
    assert calculator().fn("t", {"expression": "1/0"}).startswith("error")


def test_plan_act_loop_answers_with_tool_results() -> None:
    search, seen = recorder("search", out="[d1] Paris is the capital of France.")
    model = ScriptedModel(
        plan_policy(
            [
                ("search", {"query": "capital of France"}),
                ("calculator", {"expression": "12 * 3.5"}),
                "Paris [d1]; 12*3.5={last}",
            ]
        )
    )
    g = build_graph(model, [search, calculator()], checkpointer=InMemorySaver())
    s = run(g, {"tenant_id": "acme", "question": "q"})
    assert s["status"] == "done" and s["stop_reason"] == "final"
    assert s["answer"] == "Paris [d1]; 12*3.5=42.0"
    assert s["steps"] == 3 and s["tokens"] == 3 * 120 and model.calls == 3
    assert seen == [("acme", {"query": "capital of France"})]


def test_tenant_comes_from_state_not_model_args() -> None:
    search, seen = recorder("search")
    model = ScriptedModel(plan_policy([("search", {"query": "x", "tenant_id": "evil"}), "done"]))
    run(build_graph(model, [search]), {"tenant_id": "acme", "question": "q"})
    assert seen[0][0] == "acme"


@pytest.mark.parametrize(
    ("budget", "reason", "calls"),
    [
        (Budget(max_steps=3), "max_steps", 3),
        (Budget(max_tokens=2500), "max_tokens", 3),  # 1200 tokens per call
        (Budget(max_usd=0.01), "max_usd", 3),  # sonnet price: $0.004 per call
    ],
)
def test_budgets_stop_and_degrade(budget: Budget, reason: str, calls: int) -> None:
    model = ScriptedModel(loop_policy(), name="claude-sonnet-5-5")
    s = run(build_graph(model, [calculator()], budget), {"tenant_id": "a", "question": "q"})
    assert s["status"] == "degraded" and s["stop_reason"] == reason
    assert model.calls == calls
    assert s["answer"].startswith(f"[partial: budget {reason} reached]")
    assert "3.0" in s["answer"]  # evidence from the last observations survives


def test_cost_usd() -> None:
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 100_000) == pytest.approx(3.0)
    assert cost_usd("unknown", 10, 10) == 0.0


def test_hitl_interrupts_before_flagged_tool_and_resumes() -> None:
    ticket, seen = recorder("create_ticket", flagged=True, out="TICKET-1")
    model = ScriptedModel(plan_policy([("create_ticket", {"title": "x"}), "filed {last}"]))
    g = build_graph(model, [ticket], checkpointer=InMemorySaver())
    s = run(g, {"tenant_id": "a", "question": "q"})
    assert "__interrupt__" in s and seen == []
    assert s["__interrupt__"][0].value["tool_calls"][0]["name"] == "create_ticket"
    s = run(g, Command(resume={"approved": True}))
    assert seen == [("a", {"title": "x"})] and s["answer"] == "filed TICKET-1"


def test_hitl_reject_skips_tool() -> None:
    ticket, seen = recorder("create_ticket", flagged=True)
    model = ScriptedModel(plan_policy([("create_ticket", {}), "gave up: {last}"]))
    g = build_graph(model, [ticket], checkpointer=InMemorySaver())
    run(g, {"tenant_id": "a", "question": "q"})
    s = run(g, Command(resume={"approved": False}))
    assert seen == [] and s["answer"] == "gave up: rejected by human reviewer"


def test_unflagged_tools_do_not_interrupt() -> None:
    model = ScriptedModel(plan_policy([("calculator", {"expression": "1+1"}), "{last}"]))
    s = run(
        build_graph(model, [calculator()], checkpointer=InMemorySaver()),
        {"tenant_id": "a", "question": "q"},
    )
    assert "__interrupt__" not in s and s["answer"] == "2.0"


def test_tool_errors_and_unknown_tools_are_observations() -> None:
    def boom(_t: str, _a: dict[str, Any]) -> str:
        raise RuntimeError("db down")

    bad = Tool(ToolSpec("search", "", {}), boom)
    model = ScriptedModel(plan_policy([("search", {}), ("nope", {}), "{last}"]))
    s = run(build_graph(model, [bad]), {"tenant_id": "a", "question": "q"})
    tools = [m["content"] for m in s["messages"] if m["role"] == "tool"]
    assert tools == ["error: RuntimeError: db down", "error: unknown tool nope"]


def test_to_anthropic_groups_tool_results_and_replays_raw() -> None:
    raw = [
        {"type": "tool_use", "id": "a", "name": "x", "input": {}},
        {"type": "tool_use", "id": "b", "name": "x", "input": {}},
    ]
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [], "raw": raw},
        {"role": "tool", "tool_call_id": "a", "content": "1"},
        {"role": "tool", "tool_call_id": "b", "content": "2"},
    ]
    out = _to_anthropic(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"] is raw
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["a", "b"]
