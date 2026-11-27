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
- guardrails (Phase 5): PII at the edges and spotlighting (ADR 009); the tool-call policy and
  plan/data separation with taint (ADR 010). Policy verdicts are made in `plan`, before the
  approval pause, so a human never reviews a call the policy already refuses.

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
from agent.governance import DENIAL_PREFIX, Governance
from agent.memory import MemoryItem
from agent.model import ChatModel, Decision, cost_usd
from agent.reflection import Verifier, reflection_prompt
from agent.state import AgentState, Budget
from agent.telemetry import TENANT, ga, provider_for
from agent.telemetry import tracer as default_tracer
from agent.tool_retrieval import ToolSelector
from agent.tools import Tool
from guardrails import RULE, Guardrails, Ledger, spotlight
from guardrails.taint import EXTRACT, PLAN_RULE, QUARANTINE, handle, substitute, validate

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
    governance: Governance | None = None,
    tool_selector: ToolSelector | None = None,
    guardrails: Guardrails | None = None,
) -> Any:
    tr = tracer or default_tracer()
    provider = provider_for(model.name)
    budget = budget or Budget()
    summarize = summarizer or extractive_summarizer()
    by_name: Mapping[str, Tool] = {t.spec.name: t for t in tools}
    specs = [t.spec for t in tools]

    policy = guardrails.policy if guardrails else None
    quarantine = guardrails.quarantine if guardrails else None
    untrusted = guardrails.untrusted_tools if guardrails else frozenset()

    def denied(state: AgentState) -> dict[str, str]:
        return dict(state["messages"][-1].get("denied") or {})

    def needs_human(state: AgentState, c: dict[str, Any]) -> bool:
        if c["id"] in denied(state):
            return False
        tool = by_name.get(c["name"])
        if tool is None:
            return False
        if tool.requires_approval:
            return True
        return bool(
            governance and governance.needs_approval(state.get("role"), c["name"], c["args"])
        )

    def offered(state: AgentState) -> list[Any]:
        """Tool specs the model sees: the role's allowlist, then (flag) the top-k for the task."""
        visible = (
            specs
            if governance is None
            else [t.spec for t in governance.visible(state.get("role"), list(tools))]
        )
        if quarantine is not None:
            visible = [*visible, EXTRACT]
        if tool_selector is None:
            return visible
        used = [
            c["name"]
            for m in state.get("messages", [])
            if m.get("role") == "assistant"
            for c in m.get("tool_calls") or []
        ]
        return tool_selector.select(state.get("question", ""), visible, used)

    def start(state: AgentState) -> AgentState:
        if state.get("messages"):
            return {}
        system, ids, question, redacted = SYSTEM, [], state["question"], []
        if guardrails and guardrails.pii:  # P5.1 in: before the model, memory or a checkpoint
            red = guardrails.pii.redact(question)
            question, redacted = red.text, list(red.detectors)
        if guardrails and guardrails.spotlight_tools:
            system = f"{SYSTEM}\n\n{RULE}"
        if quarantine is not None:
            system = f"{system}\n\n{PLAN_RULE}"
        if recall is not None:
            items = recall(state["tenant_id"], question)
            if items:
                lessons = "\n".join(i.render() for i in items)
                system = f"{system}\n\nLessons from earlier tasks (may not apply):\n{lessons}"
                ids = [i.id for i in items]
        return {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
            "question": question,
            "redactions": redacted,
            "memory_ids": ids,
            "reflections": 0,
            "summary": "",
            "compacted_upto": 0,
            "variables": {},
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
            d: Decision = model.decide(v, offered(state))
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
        if policy is not None and d.tool_calls:
            done = [m for m in msgs if m["role"] == "assistant"]
            prior = sum(
                1
                for m in done
                for c in m.get("tool_calls") or []
                if c["name"] in policy.sinks and c["id"] not in (m.get("denied") or {})
            )
            variables = state.get("variables", {})
            verdicts = policy.check_calls(
                msg["tool_calls"],
                question=state["question"],
                ledger=Ledger.from_transcript(msgs, untrusted, variables),
                variables=variables,
                prior_sink_calls=prior,
            )
            if verdicts:
                msg["denied"] = {k: v.rule for k, v in verdicts.items()}
                events = [
                    {"call_id": c["id"], "tool": c["name"], "rule": verdicts[c["id"]].rule}
                    for c in msg["tool_calls"]
                    if c["id"] in verdicts
                ]
                out["policy_events"] = events
                total = len(events) + sum(len(m.get("denied") or {}) for m in done)
                if total >= policy.max_denials:  # trace-level stop: persistent injection
                    msg.pop("denied", None)
                    msg |= {"tool_calls": [], "raw": None, "content": ""}
                    stop = f"[stopped: tool-call policy denied {total} call(s)]"
                    out |= {"status": "done", "stop_reason": "policy", "answer": stop}
                    return out
        if d.final:
            answer = d.text
            if quarantine is not None:  # P5.3: values are filled in after the plan is fixed
                answer, _ = substitute(answer, state.get("variables", {}))
            if guardrails and guardrails.pii:  # P5.1 out
                red = guardrails.pii.redact(answer)
                answer, msg["content"] = red.text, red.text
                if red.detectors:  # raw blocks hold the unredacted text; rebuilt from content
                    msg["raw"] = None
                out["redactions"] = list(red.detectors)
            out |= {"status": "done", "stop_reason": "final", "answer": answer}
        return out

    def route(state: AgentState) -> str:
        if state.get("status") == "degraded":
            return "degrade"
        if state.get("status") == "done":
            return "reflect" if verifiers else END
        calls = state["messages"][-1]["tool_calls"]
        return "approve" if any(needs_human(state, c) for c in calls) else "act"

    def approve(state: AgentState) -> AgentState:
        # interrupt() must come first: on resume this node re-runs from the top.
        calls = state["messages"][-1]["tool_calls"]
        flagged = [c for c in calls if needs_human(state, c)]
        if not flagged:  # a checkpoint resumed after a policy change; nothing to review
            return {}
        decision = interrupt({"tool_calls": flagged, "role": state.get("role")})
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        who = (
            str(decision.get("approver") or "reviewer")
            if isinstance(decision, dict)
            else "reviewer"
        )
        if approved:
            return {"approvals": {**state.get("approvals", {}), **{c["id"]: who for c in flagged}}}
        if governance is not None:
            for c in flagged:
                governance.record_rejection(
                    c["name"],
                    tenant_id=state["tenant_id"],
                    role=state.get("role"),
                    args=c["args"],
                    call_id=c["id"],
                    approver=who,
                )
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
        blocked = dict(last.get("denied") or {})
        variables = dict(state.get("variables", {}))
        q_tokens, q_usd = 0, 0.0
        for c in last["tool_calls"]:
            if c["id"] in blocked:
                if governance is not None:
                    governance.record_rejection(
                        c["name"],
                        tenant_id=state["tenant_id"],
                        role=state.get("role"),
                        args=c["args"],
                        call_id=c["id"],
                        approver=f"policy:{blocked[c['id']]}",
                    )
                obs = f"{DENIAL_PREFIX}policy {blocked[c['id']]}: this call is not allowed"
                out.append(
                    {"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": obs}
                )
                continue
            if quarantine is not None and c["name"] == EXTRACT.name:
                obs, var, used = extract(c["args"], variables)
                q_tokens += used[0]
                q_usd += used[1]
                if var is not None:
                    variables[var[0]] = var[1]
                out.append(
                    {"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": obs}
                )
                continue
            args = c["args"]
            if quarantine is not None:
                args, _ = substitute(args, variables)
            tool = by_name.get(c["name"])
            with tr.start_as_current_span(f"execute_tool {c['name']}") as span:
                span.set_attribute(ga.GEN_AI_OPERATION_NAME, "execute_tool")
                span.set_attribute(ga.GEN_AI_TOOL_NAME, c["name"])
                span.set_attribute(ga.GEN_AI_TOOL_CALL_ID, c["id"])
                span.set_attribute(TENANT, state["tenant_id"])
                if tool is None:
                    obs = f"error: unknown tool {c['name']}"
                elif governance is not None:
                    obs = governance.execute(
                        tool,
                        tenant_id=state["tenant_id"],
                        role=state.get("role"),
                        args=args,
                        call_id=c["id"],
                        approver=state.get("approvals", {}).get(c["id"]),
                    )
                else:
                    try:
                        obs = tool.fn(state["tenant_id"], args)
                    except Exception as e:  # tool failure is an observation, not a crash
                        obs = f"error: {type(e).__name__}: {e}"[:500]
                if obs.startswith(DENIAL_PREFIX):
                    span.set_attribute("error.type", "tool_denied")
                elif obs.startswith("error"):
                    span.set_attribute("error.type", "tool_error")
            if compaction:
                obs = truncate(obs, compaction.tool_result_chars)
            if (
                guardrails
                and c["name"] in guardrails.spotlight_tools
                and not obs.startswith((DENIAL_PREFIX, "error"))
            ):
                obs = spotlight(obs)
            if (
                quarantine is not None
                and c["name"] in untrusted
                and not obs.startswith((DENIAL_PREFIX, "error"))
            ):  # P5.3: the planner gets a handle; the data stays in state, tainted
                name = f"$v{len(variables) + 1}"
                variables[name] = {"value": obs, "source": c["name"], "tainted": True}
                obs = handle(name, c["name"], obs)
            out.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": obs})
        res: AgentState = {"messages": out}
        if quarantine is not None:
            res["variables"] = variables
        if q_tokens:
            res["tokens"] = state.get("tokens", 0) + q_tokens
            res["usd"] = state.get("usd", 0.0) + q_usd
        return res

    def extract(
        args: dict[str, Any], variables: dict[str, dict[str, Any]]
    ) -> tuple[str, tuple[str, dict[str, Any]] | None, tuple[int, float]]:
        """Quarantined read: a tool-less model over one variable, output type-checked."""
        assert quarantine is not None
        src = variables.get(str(args.get("var", "")))
        kind = str(args.get("type", "text"))
        if src is None:
            return f"error: unknown variable {args.get('var')!r}", None, (0, 0.0)
        with tr.start_as_current_span(f"chat {quarantine.name}") as span:
            span.set_attribute(ga.GEN_AI_OPERATION_NAME, "chat")
            span.set_attribute(ga.GEN_AI_REQUEST_MODEL, quarantine.name)
            span.set_attribute("pap.guardrail.quarantine", True)
            d = quarantine.decide(
                [
                    {"role": "system", "content": QUARANTINE.format(type=kind)},
                    {
                        "role": "user",
                        "content": f"Question: {args.get('question', '')}\n\n"
                        + spotlight(str(src["value"])),
                    },
                ],
                [],
            )
        used = (
            d.input_tokens + d.output_tokens,
            cost_usd(quarantine.name, d.input_tokens, d.output_tokens),
        )
        value = validate(kind, d.text)
        if value is None:
            return f"error: extract found no valid {kind}", None, used
        name = f"$v{len(variables) + 1}"
        var = {"value": value, "source": f"extract:{src['source']}", "tainted": True, "type": kind}
        return f"{name} = extracted {kind} (untrusted, validated)", (name, var), used

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
