import math

from evals.retrieval.metrics import bootstrap_ci, dedupe_docs, ndcg_at, recall_at


def test_dedupe_keeps_first_rank() -> None:
    assert dedupe_docs(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_and_ndcg() -> None:
    ranked = ["x", "a", "y", "b"]
    assert recall_at(ranked, {"a", "b"}, 2) == 0.5
    assert recall_at(ranked, {"a", "b"}, 4) == 1.0
    assert ndcg_at(["a", "b"], {"a", "b"}, 10) == 1.0
    expected = (1 / math.log2(3)) / (1 + 1 / math.log2(3))
    assert math.isclose(ndcg_at(ranked, {"a", "b"}, 2), expected)
    assert ndcg_at(ranked, set(), 10) == 0.0


def test_bootstrap_ci_brackets_mean() -> None:
    vals = [0.0, 1.0] * 50
    lo, hi = bootstrap_ci(vals)
    assert lo < 0.5 < hi
