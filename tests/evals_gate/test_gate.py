from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from agent.model import Decision, ScriptedModel
from evals import gate as G
from evals import judge as J
from evals import trajectory as T
from evals.agent.hotpot import parse_cmu_row, parse_hf_row

CFG = tomllib.loads(Path("evals/gate.toml").read_text())


def rec(
    qid: str,
    answer: str,
    f1: float,
    calls: list[tuple[str, dict[str, Any]]],
    results: list[str],
    status: str = "done",
    sup: tuple[str, ...] = ("A", "B"),
) -> dict[str, Any]:
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "q"}]
    for (name, args), res in zip(calls, results, strict=True):
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "name": name, "args": args}],
            }
        )
        msgs.append({"role": "tool", "name": name, "tool_call_id": "1", "content": res})
    return {
        "qid": qid,
        "question": "q?",
        "gold": "x",
        "supporting": list(sup),
        "outcome": {"answer": answer, "f1": f1, "usd": 0.001, "status": status},
        "messages": msgs,
    }


def test_supporting_titles_parsed() -> None:
    hf = {
        "id": "1",
        "question": "q",
        "answer": "a",
        "context": {"title": ["A"], "sentences": [["x"]]},
        "supporting_facts": {"title": ["A", "A", "B"], "sent_id": [0, 1, 0]},
    }
    cmu = {
        "_id": "2",
        "question": "q",
        "answer": "a",
        "context": [["A", ["x"]]],
        "supporting_facts": [["B", 0], ["A", 1]],
    }
    assert parse_hf_row(hf).supporting == ("A", "B")
    assert parse_cmu_row(cmu).supporting == ("B", "A")


def test_trajectory_metrics_and_mast() -> None:
    good = rec("g", "Paris [A] [B]", 1.0, [("search", {"query": "p"})], ["[A] a\n[B] b"])
    m = T.metrics(good)
    assert (m.tool_calls, m.redundant_calls, m.evidence_recall, m.citation_precision) == (
        1,
        0,
        1.0,
        1.0,
    )
    assert m.grounded and not m.degraded
    # repeated identical search, never saw B, cited an id no tool returned, long answer
    bad = rec(
        "b",
        "it might be one of several long things here maybe Lyon or not sure [Z]",
        0.0,
        [("search", {"query": "p"}), ("search", {"query": "p"})],
        ["[A] a", "[A] a"],
    )
    mb = T.metrics(bad)
    assert mb.redundant_calls == 1 and mb.evidence_recall == 0.5 and not mb.grounded
    assert T.mast_tags(bad, mb) == ["FM-1.1", "FM-1.3", "FM-3.1", "FM-3.2"]
    # had all evidence, grounded, still wrong -> incorrect verification
    wrong = rec("w", "Lyon [A]", 0.0, [("search", {"query": "p"})], ["[A] a\n[B] b"])
    assert T.mast_tags(wrong, T.metrics(wrong)) == ["FM-3.3"]
    deg = rec("d", "", 0.0, [("fetch_doc", {"doc_id": "A"})], ["text"], status="degraded")
    assert "FM-1.5" in T.mast_tags(deg, T.metrics(deg))
    s = T.summarize([good, bad, wrong])
    assert s["failures"] == 2 and s["mast_histogram"] == {
        "FM-1.1": 1,
        "FM-1.3": 1,
        "FM-3.1": 1,
        "FM-3.2": 1,
        "FM-3.3": 1,
    }
    # a judge verdict overrides the F1 failure rule
    assert T.summarize([wrong], {"w": True})["failures"] == 0
    assert "FM-3.3" in T.to_markdown(s)


def test_kappa_known_values() -> None:
    a = [True, True, False, False]
    assert J.cohen_kappa(a, a) == 1.0
    assert J.cohen_kappa(a, [not x for x in a]) == -1.0
    # 20 items: po = 0.7, pe = 0.5 -> kappa 0.4
    h = [True] * 10 + [False] * 10
    j = [True] * 7 + [False] * 3 + [True] * 3 + [False] * 7
    assert J.cohen_kappa(h, j) == pytest.approx(0.4)
    k, lo, hi = J.kappa_ci(h, j)
    assert lo <= k <= hi


def test_judge_parse_and_cached_prompt() -> None:
    assert J.parse_verdict("CORRECT.") is True
    assert J.parse_verdict("incorrect") is False and J.parse_verdict("?") is None
    seen: list[str] = []

    def policy(msgs: Any) -> Decision:
        seen.append(msgs[-1]["content"])
        return Decision(text="CORRECT")

    r = rec("g", "Paris [A]", 1.0, [], [])
    assert J.judge(ScriptedModel(policy), [r]) == {"g": True}
    assert "[A]" not in seen[0]  # citations stripped before judging


def test_label_pool_roundtrip_hides_scores(tmp_path: Path) -> None:
    p = tmp_path / "pool.csv"
    J.write_label_pool(p, [rec("a", "Paris", 0.3, [], []), rec("b", "Lyon", 0.0, [], [])])
    text = p.read_text()
    assert "0.3" not in text and "human_correct" in text
    p.write_text(text.replace("Paris,,", "Paris,1,").replace("Lyon,,", "Lyon,no,"))
    assert J.load_labels(p) == {"a": True, "b": False}
    assert J.load_labels(tmp_path / "missing.csv") == {}


def _cur(vals: list[float], usd: float = 0.001) -> dict[str, dict[str, float]]:
    return {
        str(i): {"f1": v, "usd": usd, "evidence_recall": 1.0, "redundant_calls": 0.0}
        for i, v in enumerate(vals)
    }


def test_gate_paired_bootstrap() -> None:
    base = _cur([0.5, 1.0, 0.0, 1.0] * 25)
    same, _ = G.run_gate(CFG, base, base, {"n": 0}, 0.0)
    assert all(r["status"] == "ok" for r in same if r["metric"] != "judge_correct")
    worse = _cur([0.0, 1.0, 0.0, 0.5] * 25)  # -0.25 F1 on half the items
    rows, fails = G.run_gate(CFG, worse, base, {"n": 0}, 0.0)
    assert next(r for r in rows if r["metric"] == "f1")["status"] == "REGRESSED"
    assert len(fails) == 1 and fails[0].startswith("f1")
    # report-only metrics regress without failing the gate
    pricey = _cur([0.5, 1.0, 0.0, 1.0] * 25, usd=0.002)
    rows, fails = G.run_gate(CFG, pricey, base, {"n": 0}, 0.0)
    assert next(r for r in rows if r["metric"] == "usd")["status"] == "REGRESSED" and not fails


def test_gate_scalar_fallback_cost_cap_and_kappa() -> None:
    rows, fails = G.run_gate(CFG, _cur([0.4] * 10), {}, {"n": 0}, 0.0)
    assert fails and "scalar" in next(r for r in rows if r["metric"] == "f1")["why"]
    _, fails = G.run_gate(CFG, _cur([0.6] * 10), {}, {"n": 0}, 5.0)
    assert fails == ["spend $5.00 > cost cap $1.00"]
    calib = {"n": 100, "judge_kappa": [0.7, 0.6, 0.8]}
    cfg = {
        **CFG,
        "metric": {
            **CFG["metric"],
            "judge_correct": {**CFG["metric"]["judge_correct"], "enforce": True},
        },
    }
    rows, _ = G.run_gate(cfg, _cur([0.6] * 10), {}, calib, 0.0)
    assert next(r for r in rows if r["metric"] == "judge_correct")["enforced"] is True
    rows, _ = G.run_gate(cfg, _cur([0.6] * 10), {}, {"n": 40, "judge_kappa": [0.9, 0.8, 1]}, 0.0)
    assert next(r for r in rows if r["metric"] == "judge_correct")["enforced"] is False
