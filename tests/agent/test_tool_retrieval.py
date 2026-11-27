"""P3.3: the selector narrows what the model sees, never widens it, keeps used tools."""

from __future__ import annotations

import asyncio
from typing import Any

from agent.governance import Governance
from agent.graph import build_graph
from agent.model import ScriptedModel, ToolSpec
from agent.tool_retrieval import ToolSelector
from agent.tools import Tool
from evals.tools.catalog import catalog
from evals.tools.run import run
from retrieval.embedding import HashEmbedder
from tests.agent.scripted import plan_policy


def tools(names: list[str]) -> list[Tool]:
    return [Tool(ToolSpec(n, f"{n.replace('_', ' ')} tool", {}), lambda t, a: "ok") for n in names]


class Spy(ScriptedModel):
    def __init__(self, policy: Any) -> None:
        super().__init__(policy)
        self.offered: list[list[str]] = []

    def decide(self, messages: Any, tools: Any) -> Any:
        self.offered.append([t.name for t in tools])
        return super().decide(messages, tools)


def test_small_catalogs_are_untouched() -> None:
    specs, _ = catalog(5)
    assert ToolSelector(HashEmbedder(), k=5).select("anything", specs) == specs


def test_selects_top_k_plus_used_and_always_in_stable_order() -> None:
    specs, _ = catalog(50)
    sel = ToolSelector(HashEmbedder(), k=3, always=frozenset({"calculator"}))
    got = sel.select("search for shipment parcel carriers", specs, used=["fetch_doc"])
    names = [s.name for s in got]
    assert "search_shipment" in names and {"calculator", "fetch_doc"} <= set(names)
    assert len(names) <= 5
    assert names == [s.name for s in specs if s.name in names]  # catalog order kept


def test_graph_offers_only_selected_tools_and_keeps_a_used_one() -> None:
    ts = tools(["list_invoice", "list_ticket", "get_shipment", "list_employee", "export_metric"])
    model = Spy(plan_policy([("list_invoice", {}), ("list_invoice", {}), "done"]))
    g = build_graph(model, ts, tool_selector=ToolSelector(HashEmbedder(), k=1))
    s = asyncio.run(
        g.ainvoke(
            {"tenant_id": "acme", "question": "list invoice amounts due"},
            {"configurable": {"thread_id": "acme:t"}},
        )
    )
    assert s["answer"] == "done"
    assert all(o == ["list_invoice"] for o in model.offered)


def test_selection_never_widens_the_role_allowlist(tmp_path: Any) -> None:
    gov = Governance.from_yaml(audit_path=str(tmp_path / "a.db"))
    ts = tools(["search", "fetch_doc", "calculator", "create_ticket", "list_invoice", "x1"])
    model = Spy(plan_policy(["done"]))
    sel = ToolSelector(HashEmbedder(), k=2, always=frozenset({"create_ticket"}))
    g = build_graph(model, ts, governance=gov, tool_selector=sel)
    asyncio.run(
        g.ainvoke(
            {"tenant_id": "acme", "role": "researcher", "question": "create a ticket"},
            {"configurable": {"thread_id": "acme:t2"}},
        )
    )
    assert "create_ticket" not in model.offered[0]  # always-include can't bypass RBAC
    assert set(model.offered[0]) <= {"search", "fetch_doc", "calculator"}
    gov.close()


def test_scaling_eval_runs_and_saves_tokens() -> None:
    rows = run(HashEmbedder(), k=5, sizes=(25, 100))
    assert [r["n_tools"] for r in rows] == [25, 100]
    for r in rows:
        assert 0 <= r["recall_at_k"] <= 1 and r["tokens_selected"] < r["tokens_all"]
