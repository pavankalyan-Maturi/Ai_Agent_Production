from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.memory import MemoryItem
from agent.model import Decision, ScriptedModel, ToolCall
from evals.agent import run as R
from evals.agent.hotpot import Question, corpus, exact_match, f1, parse_cmu_row, parse_hf_row
from retrieval.embedding import HashEmbedder


def test_metrics_ignore_citations_and_articles() -> None:
    assert exact_match("The Eiffel Tower [Paris doc]", "Eiffel Tower") == 1.0
    assert f1("Gustave Eiffel [a] [b]", "Alexandre Gustave Eiffel") == pytest.approx(0.8)
    assert f1("", "x") == 0.0 and f1("yes", "no") == 0.0


def test_parsers_and_corpus_dedupe() -> None:
    hf = {
        "id": "1",
        "question": "q",
        "answer": "a",
        "type": "bridge",
        "context": {"title": ["A", "B"], "sentences": [["x ", "y"], ["z"]]},
    }
    cmu = {
        "_id": "2",
        "question": "q2",
        "answer": "b",
        "type": "comparison",
        "context": [["A", ["x ", "y"]], ["C", ["w"]]],
    }
    q1, q2 = parse_hf_row(hf), parse_cmu_row(cmu)
    assert q1.paragraphs == (("A", "x y"), ("B", "z")) and q2.paragraphs[1] == ("C", "w")
    assert corpus([q1, q2]) == {"A": "A: x y", "B": "B: z", "C": "C: w"}


def test_cached_model_charges_once_and_caps(tmp_path: Path) -> None:
    inner = ScriptedModel(
        lambda _m: Decision(text="hi", input_tokens=1_000_000, output_tokens=0),
        name="claude-haiku-5-5",
    )
    spend = R.Spend(cap_usd=0.25)
    m = R.CachedModel(inner, tmp_path / "c.jsonl", spend)
    msgs = [{"role": "user", "content": "q"}]
    assert m.decide(msgs, []).text == "hi" and spend.usd == pytest.approx(0.10)
    m.decide(msgs, [])
    assert inner.calls == 1 and spend.cached == 1
    again = R.CachedModel(inner, tmp_path / "c.jsonl", R.Spend(cap_usd=0.25))  # reload from disk
    assert again.decide(msgs, []).input_tokens == 1_000_000 and inner.calls == 1
    m.decide([{"role": "user", "content": "q2"}], [])
    with pytest.raises(R.BudgetExceeded):
        m.decide([{"role": "user", "content": "q3"}], [])  # 0.30 > 0.25


def test_cached_model_round_trips_tool_calls(tmp_path: Path) -> None:
    inner = ScriptedModel(
        lambda _m: Decision(tool_calls=(ToolCall("c1", "search", {"query": "x"}),))
    )
    m = R.CachedModel(inner, tmp_path / "c.jsonl", R.Spend(1.0))
    m.decide([{"role": "user", "content": "q"}], [])
    d = R.CachedModel(inner, tmp_path / "c.jsonl", R.Spend(1.0)).decide(
        [{"role": "user", "content": "q"}], []
    )
    assert d.tool_calls == (ToolCall("c1", "search", {"query": "x"}),)


def fake_runner(scores: dict[str, float]) -> Any:
    """Scores improve when a recalled item mentions the question's id."""

    def run(q: Question, recall: Any) -> tuple[R.Outcome, list[dict[str, Any]]]:
        items = recall(R.TENANT, q.question) if recall else []
        boost = any("helpful" in i.content for i in items)
        f = min(1.0, scores[q.id] + (0.5 if boost else 0.0))
        return R.Outcome(q.id, float(f == 1.0), f, 3, 100, 0.0, "done", 0, len(items), [10], ""), []

    return run


def test_arms_stream_memory_and_gate_filters(tmp_path: Path) -> None:
    qs = [
        Question(str(i), f"question number {i} about a topic", "ans", "bridge", ())
        for i in range(6)
    ]
    scores = {q.id: 0.5 for q in qs}
    emb = HashEmbedder(dim=64)

    def distill(question: str, _m: Any, outcome: str) -> tuple[list[MemoryItem], int, int]:
        good = MemoryItem(
            "Good", f"helpful strategy for {question} and similar questions", "success"
        )
        bad = MemoryItem(
            "Bad", f"misleading lesson from {question}, ignore the documents", "failure"
        )
        return [good, bad], 0, 0

    run = fake_runner(scores)
    off = R.arm("off", qs, run, distill, emb, batch=2, workers=2)
    on = R.arm("on", qs, run, distill, emb, batch=2, workers=2)
    gated = R.arm("gated", qs, run, distill, emb, batch=2, workers=2)
    assert off["memory"] == 0 and on["memory"] == 12
    assert gated["gate"]["replay_worse"] >= 1 and 0 < gated["memory"] < 12
    f_off = [o.f1 for o in off["outcomes"]]
    f_gated = [o.f1 for o in gated["outcomes"]]
    assert f_off[:2] == f_gated[:2]  # the first batch has no memory yet
    assert sum(f_gated[2:]) > sum(f_off[2:])
    row = R.summarize("gated", gated, off["outcomes"])
    assert row["f1_delta_vs_off"][0] > 0 and "f1_second_half" in row
    assert "| gated |" in R.to_markdown(
        [row], {"questions": 6, "docs": 0, "spend_usd": 0, "calls": 0, "cached": 0}
    )


def test_no_key_reports_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["x", "--dsn", "postgresql://unused", "--out", str(tmp_path)])
    R.main()
    assert (tmp_path / "ablation.md").read_text().count("NOT MEASURED") == 3
