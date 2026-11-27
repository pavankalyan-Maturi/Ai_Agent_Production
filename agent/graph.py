"""Planner/executor loop on LangGraph.

plan --(final)--> END
plan --(tool calls, none flagged)--> act --> plan
plan --(a flagged tool)--> approve --(interrupt: human decides)--> act --> plan
plan --(budget exhausted)--> degrade --> END
plan --(final, verifiers set)--> reflect --(external check failed, retries left)--> plan

Optional parts (each off unless passed):
- compaction (P2.4): cap tool results; summarise old observations once the view is too big.
- recall (P2.5): experience-memory items added to the system prompt at start.
- verifiers (P2.6): external checks on a final answer; a failure triggers one grounded retry.

Budgets are checked before every model call, so a run never starts a call it cannot pay for
beyond one step's worth. degrade answers from the observations so far without calling the model.
Tool calls in a crashed `act` step re-run on resume (at-least-once); flagged tools are therefore
the ones to make idempotent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer, interrupt
from opentelemetry.trace import Tracer

from agent.compaction import (
    CompactionPolicy,
    Summarizer,
    cut_point,
    estimate_tokens,
    extractive_summarizer,
    truncate,
    view,
)
from agent.memory import MemoryItem
from agent.model import ChatModel, Decision, cost_usd
from agent.reflection import Verifier, reflection_prompt
from agent.state import AgentState, Budget
from agent.telemetry import TENANT, ga, provider_for
from agent.telemetry import tracer as default_tracer
from agent.tools import Tool

Recaller = Callable[[str, str], list[MemoryItem]]  # (tenant_id, question) -> items

SYSTEM = (
    "You answer questions using the tools. Search first, fetch documents when snippets are not "
    "enough, use the calculator for arithmetic. Cite doc_ids. Answer concisely when done."
)


def build_graph(
    model: ChatModel,
    tools: Sequence[Tool],
    budget: Budget | None = None,
    checkpointer: Checkpointer = None,
    *,
    compaction: CompactionPolicy | None = None,
    summarizer: Summarizer | None = None,
    recall: Recaller | None = None,
    verifiers: Sequence[Verifier] = (),
    max_reflections: int = 1,
    tracer: Tracer | None = None,
) -> Any:
    tr = tracer or default_tracer()
    provider = provider_for(model.name)
    budget = budget or Budget()
    summarize = summarizer or extractive_summarizer()
    by_name: Mapping[str, Tool] = {t.spec.name: t for t in tools}
    specs = [t.spec for t in tools]

    def start(state: AgentState) -> AgentState:
        if state.get("messages"):
            return {}
        system, ids = SYSTEM, []
        if recall is not None:
            items = recall(state["tenant_id"], state["question"])
            if items:
                lessons = "\n".join(i.render() for i in items)
                system = f"{SYSTEM}\n\nLessons from earlier tasks (may not apply):\n{lessons}"
                ids = [i.id for i in items]
        return {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": state["question"]},
            ],
            "memory_ids": ids,
            "reflections": 0,
            "summary": "",
            "compacted_upto": 0,
            "steps": 0,
            "tokens": 0,
            "usd": 0.0,
            "status": "running",
        }

    def plan(state: AgentState) -> AgentState:
        reason = budget.exhausted(state)
        if reason:
            return {"status": "degraded", "stop_reason": reason}
        msgs = state["messages"]
        upto, summary = state.get("compacted_upto", 0), state.get("summary", "")
        s_in = s_out = 0
        v = view(msgs, upto, summary)
        if compaction and estimate_tokens(v) > compaction.threshold_tokens:
            cut = cut_point(msgs, compaction.keep_last)
            obs = [m["content"] for m in msgs[upto:cut] if m["role"] == "tool"]
            if obs:
                summary, s_in, s_out = summarize(summary, obs)
                upto = cut
                v = view(msgs, upto, summary)
        with tr.start_as_current_span(f"chat {model.name}") as span:
            span.set_attribute(ga.GEN_AI_OPERATION_NAME, "chat")
            span.set_attribute(ga.GEN_AI_PROVIDER_NAME, provider)
            span.set_attribute(ga.GEN_AI_REQUEST_MODEL, model.name)
            span.set_attribute(TENANT, state["tenant_id"])
            d: Decision = model.decide(v, specs)
            span.set_attribute(ga.GEN_AI_USAGE_INPUT_TOKENS, d.input_tokens)
            span.set_attribute(ga.GEN_AI_USAGE_OUTPUT_TOKENS, d.output_tokens)
        d_in, d_out = d.input_tokens + s_in, d.output_tokens + s_out
        msg = {
            "role": "assistant",
            "content": d.text,
            "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in d.tool_calls],
            "raw": d.raw,
        }
        out: AgentState = {
            "messages": [msg],
            "steps": state.get("steps", 0) + 1,
            "tokens": state.get("tokens", 0) + d_in + d_out,
            "usd": state.get("usd", 0.0) + cost_usd(model.name, d_in, d_out),
            "context_tokens": [estimate_tokens(v)],
            "compacted_upto": upto,
            "summary": summary,
        }
        if d.final:
            out |= {"status": "done", "stop_reason": "final", "answer": d.text}
        return out

    def route(state: AgentState) -> str:
        if state.get("status") == "degraded":
            return "degrade"
        if state.get("status") == "done":
            return "reflect" if verifiers else END
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
            with tr.start_as_current_span(f"execute_tool {c['name']}") as span:
                span.set_attribute(ga.GEN_AI_OPERATION_NAME, "execute_tool")
                span.set_attribute(ga.GEN_AI_TOOL_NAME, c["name"])
                span.set_attribute(ga.GEN_AI_TOOL_CALL_ID, c["id"])
                span.set_attribute(TENANT, state["tenant_id"])
                if tool is None:
                    obs = f"error: unknown tool {c['name']}"
                else:
                    try:
                        obs = tool.fn(state["tenant_id"], c["args"])
                    except Exception as e:  # tool failure is an observation, not a crash
                        obs = f"error: {type(e).__name__}: {e}"[:500]
                if obs.startswith("error"):
                    span.set_attribute("error.type", "tool_error")
            if compaction:
                obs = truncate(obs, compaction.tool_result_chars)
            out.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": obs})
        return {"messages": out}

    def reflect(state: AgentState) -> AgentState:
        answer = state.get("answer", "")
        for check in verifiers:
            reason = check(state["messages"], answer)
            if reason is None:
                continue
            n = state.get("reflections", 0)
            if n >= max_reflections or budget.exhausted(state):
                return {"verified": False, "failure": reason}
            return {
                "messages": [{"role": "user", "content": reflection_prompt(reason)}],
                "status": "running",
                "reflections": n + 1,
                "failure": reason,
            }
        return {"verified": True, "failure": ""}

    def after_reflect(state: AgentState) -> str:
        return "plan" if state.get("status") == "running" else END

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
    g.add_node("reflect", reflect)
    g.add_conditional_edges("plan", route, ["act", "approve", "degrade", "reflect", END])
    g.add_conditional_edges("reflect", after_reflect, ["plan", END])
    g.add_edge("approve", "act")
    g.add_edge("act", "plan")
    g.add_edge("degrade", END)
    return g.compile(checkpointer=checkpointer)
