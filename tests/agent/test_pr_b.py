"""P2.4 compaction, P2.5 experience memory + gate, P2.6 grounded reflection."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import numpy as np
from langgraph.checkpoint.memory import InMemorySaver

from agent.compaction import STUB, CompactionPolicy, cut_point, truncate, view
from agent.graph import build_graph
from agent.memory import (
    AdmissionGate,
    InMemoryBank,
    MemoryItem,
    Recall,
    RunResult,
    parse_items,
    render_trajectory,
)
from agent.model import Decision, ScriptedModel, ToolCall, ToolSpec, _to_anthropic
from agent.reflection import citations_grounded, last_tools_failed
from agent.state import Budget
from agent.tools import Tool
from retrieval.embedding import HashEmbedder
from tests.agent.scripted import n_assistant, plan_policy


def run(graph: Any, inp: Any) -> dict[str, Any]:
    return asyncio.run(graph.ainvoke(inp, {"configurable": {"thread_id": "t:1"}}))


def big_search(chars: int = 6000) -> Tool:
    def fn(_t: str, args: dict[str, Any]) -> str:
        return f"[d{args['i']}] " + ("lorem ipsum " * (chars // 12))

    return Tool(ToolSpec("search", "", {}), fn)


# ---------- P2.4 compaction ----------


def test_truncate_keeps_head_and_tail() -> None:
    t = truncate("a" * 50 + "b" * 50, 40)
    assert t.startswith("a" * 30) and t.endswith("b" * 10) and "60 chars truncated" in t
    assert truncate("short", 40) == "short"


def test_cut_point_never_splits_a_tool_group() -> None:
    msgs = [
        {"role": "system"},
        {"role": "user"},
        {"role": "assistant"},
        {"role": "tool"},
        {"role": "tool"},
        {"role": "assistant"},
    ]
    assert cut_point(msgs, keep_last=3) == 5  # would land on a tool message, moves past it


def test_view_stubs_old_tool_results_and_keeps_pairs() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "raw": [{"x": 1}],
            "tool_calls": [{"id": "a", "name": "search", "args": {}}],
        },
        {"role": "tool", "tool_call_id": "a", "content": "long " * 100},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "b", "name": "s", "args": {}}]},
        {"role": "tool", "tool_call_id": "b", "content": "new"},
    ]
    v = view(msgs, 4, "found d1")
    assert "Summary of earlier findings:\nfound d1" in v[0]["content"]
    assert v[3]["content"] == STUB and v[5]["content"] == "new"
    assert "raw" not in v[2] and msgs[2]["raw"]  # original transcript untouched
    a = _to_anthropic(v)
    assert a[1]["content"][0] == {"type": "tool_use", "id": "a", "name": "search", "input": {}}


def test_compaction_bounds_context_growth() -> None:
    steps: list[Any] = [("search", {"i": i}) for i in range(10)] + ["done"]
    off = run(
        build_graph(ScriptedModel(plan_policy(steps)), [big_search()], Budget(max_steps=20)),
        {"tenant_id": "a", "question": "q"},
    )
    pol = CompactionPolicy(tool_result_chars=2000, threshold_tokens=2500, keep_last=2)
    on = run(
        build_graph(
            ScriptedModel(plan_policy(steps)), [big_search()], Budget(max_steps=20), compaction=pol
        ),
        {"tenant_id": "a", "question": "q"},
    )
    assert off["answer"] == on["answer"] == "done"
    assert max(off["context_tokens"]) > 13_000  # grows linearly, ~1.5k tokens per step
    assert max(on["context_tokens"]) < 3_500
    assert on["compacted_upto"] > 0 and "[d0]" in on["summary"]
    assert len(on["messages"]) == len(off["messages"])  # transcript itself is not rewritten


# ---------- P2.6 grounded reflection ----------


def test_citation_verifier() -> None:
    msgs = [{"role": "tool", "name": "search", "content": "[d1] text\n[d2] more"}]
    assert citations_grounded(msgs, "x [d1]") is None
    assert "no [doc_id]" in (citations_grounded(msgs, "x") or "")
    assert "d9" in (citations_grounded(msgs, "x [d9]") or "")


def test_last_tools_failed() -> None:
    call = {"role": "assistant", "tool_calls": [{"id": "1", "name": "s", "args": {}}]}
    assert last_tools_failed([call, {"role": "tool", "content": "error: x"}], "a")
    assert last_tools_failed([call, {"role": "tool", "content": "ok"}], "a") is None


def search_tool(out: str = "[d1] Paris is in France.") -> Tool:
    return Tool(ToolSpec("search", "", {}), lambda _t, _a: out)


def test_no_reflection_when_external_check_passes() -> None:
    model = ScriptedModel(plan_policy([("search", {"query": "x"}), "Paris [d1]"]))
    s = run(
        build_graph(model, [search_tool()], verifiers=[citations_grounded]),
        {"tenant_id": "a", "question": "q"},
    )
    assert s["verified"] is True and s["reflections"] == 0 and model.calls == 2


def test_reflection_retries_once_on_external_failure() -> None:
    # Call 2 answers without a citation; after the grounded feedback, call 3 cites d1.
    def policy(messages: Sequence[dict[str, Any]]) -> Decision:
        i = n_assistant(messages)
        if i == 0:
            return Decision(tool_calls=(ToolCall("c0", "search", {"query": "x"}),))
        return Decision(text="Paris" if i == 1 else "Paris [d1]")

    model = ScriptedModel(policy)
    s = run(
        build_graph(model, [search_tool()], verifiers=[citations_grounded]),
        {"tenant_id": "a", "question": "q"},
    )
    assert s["reflections"] == 1 and s["verified"] is True and s["answer"] == "Paris [d1]"
    assert any(
        "external check failed" in m["content"].lower()
        for m in s["messages"]
        if m["role"] == "user"
    )


def test_reflection_is_capped() -> None:
    model = ScriptedModel(plan_policy([("search", {"query": "x"}), "never cites"]))
    s = run(
        build_graph(model, [search_tool()], verifiers=[citations_grounded], max_reflections=1),
        {"tenant_id": "a", "question": "q"},
    )
    assert s["reflections"] == 1 and s["verified"] is False and model.calls == 3


# ---------- P2.5 experience memory ----------

EMB = HashEmbedder(dim=128)


def item(content: str, title: str = "Bridge entities") -> MemoryItem:
    return MemoryItem(title, content, "success", "src question")


def test_bank_is_tenant_scoped_and_dedupes() -> None:
    bank = InMemoryBank()
    it = item("Search for the bridge entity first, then search its attribute.")
    v = np.asarray(EMB.embed_query(it.content))
    bank.add("a", it, v)
    bank.add("a", it, v)
    assert bank.count("a") == 1 and bank.search("b", v, 3) == []


def test_recall_injects_lessons_into_system_prompt() -> None:
    bank = InMemoryBank()
    it = item("For two-hop questions, search the first entity, then the linked one.")
    bank.add("a", it, np.asarray(EMB.embed_query("two-hop questions first entity linked")))
    seen: list[str] = []

    def policy(messages: Sequence[dict[str, Any]]) -> Decision:
        seen.append(messages[0]["content"])
        return Decision(text="ok")

    rec = Recall(bank, EMB, min_sim=-1.0)
    s = run(build_graph(ScriptedModel(policy), [], recall=rec), {"tenant_id": "a", "question": "q"})
    assert "Lessons from earlier tasks" in seen[0] and it.content in seen[0]
    assert s["memory_ids"] == [it.id]
    s = run(
        build_graph(ScriptedModel(policy), [], recall=rec, checkpointer=InMemorySaver()),
        {"tenant_id": "other", "question": "q"},
    )
    assert s["memory_ids"] == []


def test_gate_static_checks() -> None:
    bank = InMemoryBank()
    gate = AdmissionGate(bank, EMB)
    ok = item("Search each entity separately and compare their founding years.")
    assert gate.static_ok("a", ok, answer="1887")
    assert not gate.static_ok("a", item("short"), answer="")
    assert not gate.static_ok(
        "a", item("The answer is Gustave Eiffel, found in d3."), "Gustave Eiffel"
    )
    assert not gate.static_ok("a", item("Use api_key: sk-123 when the search tool fails badly"), "")
    gate.admit("a", ok, "1887", RunResult(1.0, 3), lambda _i: RunResult(1.0, 3))
    assert not gate.static_ok("a", ok, "1887")  # near-duplicate of an admitted item
    assert gate.stats == {"length": 1, "answer_leak": 1, "secret": 1, "admitted": 1, "duplicate": 1}


def test_gate_replay_with_and_without() -> None:
    bank = InMemoryBank()
    gate = AdmissionGate(bank, EMB)
    base = RunResult(score=0.5, steps=4)
    good = item("Fetch the full document when the snippet lacks the date you need.", "Fetch")
    bad = item("Answer from the first snippet without fetching documents at all.", "Fast")
    same_cheaper = item("Stop searching once both entities have been found in results.", "Stop")
    assert gate.admit("a", good, "", base, lambda _i: RunResult(1.0, 5))
    assert not gate.admit("a", bad, "", base, lambda _i: RunResult(0.0, 2))
    assert not gate.admit("a", same_cheaper, "", base, lambda _i: RunResult(0.5, 3))  # neutral
    solved = RunResult(score=1.0, steps=3)
    assert gate.admit("a", same_cheaper, "", solved, lambda _i: RunResult(1.0, 3))  # kept solved
    assert not gate.admit("a", bad, "", solved, lambda _i: RunResult(1.0, 4))  # costs a step
    assert bank.count("a") == 2 and gate.stats["replay_worse"] == 3


def test_parse_items_and_trajectory() -> None:
    text = 'Sure: [{"title": "T", "content": "C"}, {"bad": 1}, {"title": "U", "content": "D"}]'
    items = parse_items(text, "failure", "q")
    assert [(i.title, i.outcome) for i in items] == [("T", "failure")]
    assert parse_items("no json", "success", "q") == []
    traj = render_trajectory(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "search", "args": {"q": 1}}],
            },
            {"role": "tool", "content": "[d1] x"},
            {"role": "assistant", "content": "final", "tool_calls": []},
        ]
    )
    assert traj.splitlines() == ['ACTION search({"q": 1})', "OBSERVATION [d1] x", "ANSWER final"]


def test_genai_spans_carry_usage_and_no_content() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exp = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exp))
    model = ScriptedModel(
        plan_policy([("search", {"query": "secret question"}), "Paris [d1]"]),
        name="claude-haiku-5-5",
    )
    run(
        build_graph(model, [search_tool()], tracer=tp.get_tracer("t")),
        {"tenant_id": "acme", "question": "secret question"},
    )
    spans = exp.get_finished_spans()
    names = [s.name for s in spans]
    assert names == ["chat claude-haiku-5-5", "execute_tool search", "chat claude-haiku-5-5"]
    chat = spans[0].attributes or {}
    assert chat["gen_ai.provider.name"] == "anthropic" and chat["gen_ai.usage.input_tokens"] == 100
    assert chat["pap.tenant_id"] == "acme"
    flat = str([dict(s.attributes or {}) for s in spans])
    assert "secret question" not in flat and "Paris" not in flat
