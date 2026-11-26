"""Planner/executor loop on LangGraph.

plan --(final)--> END
plan --(tool calls, none flagged)--> act --> plan
plan --(a flagged tool)--> approve --(interrupt: human decides)--> act --> plan
plan --(budget exhausted)--> degrade --> END

Budgets are checked before every model call, so a run never starts a call it cannot pay for
beyond one step's worth. degrade answers from the observations so far without calling the model.
Tool calls in a crashed `act` step re-run on resume (at-least-once); flagged tools are therefore
the ones to make idempotent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer, interrupt

from agent.model import ChatModel, Decision, cost_usd
from agent.state import AgentState, Budget
from agent.tools import Tool

SYSTEM = (
    "You answer questions using the tools. Search first, fetch documents when snippets are not "
    "enough, use the calculator for arithmetic. Cite doc_ids. Answer concisely when done."
)


def build_graph(
    model: ChatModel,
    tools: Sequence[Tool],
    budget: Budget | None = None,
    checkpointer: Checkpointer = None,
) -> Any:
    budget = budget or Budget()
    by_name: Mapping[str, Tool] = {t.spec.name: t for t in tools}
    specs = [t.spec for t in tools]

    def start(state: AgentState) -> AgentState:
        if state.get("messages"):
            return {}
        return {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": state["question"]},
            ],
            "steps": 0,
            "tokens": 0,
            "usd": 0.0,
            "status": "running",
        }

    def plan(state: AgentState) -> AgentState:
        reason = budget.exhausted(state)
        if reason:
            return {"status": "degraded", "stop_reason": reason}
        d: Decision = model.decide(state["messages"], specs)
        msg = {
            "role": "assistant",
            "content": d.text,
            "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in d.tool_calls],
            "raw": d.raw,
        }
        out: AgentState = {
            "messages": [msg],
            "steps": state.get("steps", 0) + 1,
            "tokens": state.get("tokens", 0) + d.input_tokens + d.output_tokens,
            "usd": state.get("usd", 0.0) + cost_usd(model.name, d.input_tokens, d.output_tokens),
        }
        if d.final:
            out |= {"status": "done", "stop_reason": "final", "answer": d.text}
        return out

    def route(state: AgentState) -> str:
        if state.get("status") == "degraded":
            return "degrade"
        if state.get("status") == "done":
            return END
        calls = state["messages"][-1]["tool_calls"]
        flagged = any(
            by_name.get(c["name"]) and by_name[c["name"]].requires_approval for c in calls
        )
        return "approve" if flagged else "act"

    def approve(state: AgentState) -> AgentState:
        # interrupt() must come first: on resume this node re-runs from the top.
        calls = state["messages"][-1]["tool_calls"]
        flagged = [
            c for c in calls if by_name.get(c["name"]) and by_name[c["name"]].requires_approval
        ]
        decision = interrupt({"tool_calls": flagged})
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        if approved:
            return {}
        # Rejected: answer every call with a rejection so the transcript stays valid.
        return {
            "messages": [
                {
                    "role": "tool",
                    "tool_call_id": c["id"],
                    "name": c["name"],
                    "content": "rejected by human reviewer",
                }
                for c in calls
            ]
        }

    def act(state: AgentState) -> AgentState:
        last = state["messages"][-1]
        if last["role"] == "tool":  # approve() rejected: nothing to run
            return {}
        out = []
        for c in last["tool_calls"]:
            tool = by_name.get(c["name"])
            if tool is None:
                obs = f"error: unknown tool {c['name']}"
            else:
                try:
                    obs = tool.fn(state["tenant_id"], c["args"])
                except Exception as e:  # tool failure is an observation, not a crash
                    obs = f"error: {type(e).__name__}: {e}"[:500]
            out.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": obs})
        return {"messages": out}

    def degrade(state: AgentState) -> AgentState:
        obs = [m["content"] for m in state["messages"] if m["role"] == "tool"][-3:]
        partial = "\n".join(o[:300] for o in obs) or "no observations"
        reason = state.get("stop_reason")
        return {"answer": f"[partial: budget {reason} reached] Evidence so far:\n{partial}"}

    g = StateGraph(AgentState)
    g.add_node("start", start)
    g.add_node("plan", plan)
    g.add_node("approve", approve)
    g.add_node("act", act)
    g.add_node("degrade", degrade)
    g.add_edge(START, "start")
    g.add_edge("start", "plan")
    g.add_conditional_edges("plan", route, ["act", "approve", "degrade", END])
    g.add_edge("approve", "act")
    g.add_edge("act", "plan")
    g.add_edge("degrade", END)
    return g.compile(checkpointer=checkpointer)
