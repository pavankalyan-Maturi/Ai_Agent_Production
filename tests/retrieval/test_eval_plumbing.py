"""Runs the ablation code path end to end on a toy corpus with offline fakes."""

import os

import pytest

from evals.retrieval.run import build_chunks, evaluate
from evals.retrieval.scifact import Slice
from retrieval.contextual import MockContextualizer
from retrieval.embedding import HashEmbedder
from retrieval.pipeline import SearchConfig, index_chunks
from retrieval.store import PgStore
from retrieval.types import Hit

DSN = os.environ.get("PAP_TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DSN, reason="no test DB")]


class ReverseReranker:
    name = "reverse"

    def rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        return list(reversed(hits))[:top_k]


def test_ablation_plumbing() -> None:
    assert DSN
    sl = Slice(
        corpus={
            "1": ("Vitamin D", "Vitamin D supplementation reduces fractures in the elderly."),
            "2": ("Statins", "Statins lower LDL cholesterol."),
            "3": ("GNNs", "Graph networks for molecules."),
        },
        queries={"q1": "does vitamin d prevent fractures", "q2": "cholesterol drugs"},
        qrels={"q1": {"1"}, "q2": {"2"}},
    )
    e = HashEmbedder(384)
    store = PgStore(DSN, 384)
    store.migrate()
    store.delete_tenant("plumb")
    chunks = build_chunks(sl, "plumb", 96, 16, MockContextualizer(), workers=2)
    assert all(c.context for c in chunks)
    index_chunks(store, e, chunks)
    row = evaluate(store, e, None, sl, "plumb", SearchConfig(hybrid=True))
    assert row["recall@20"] == 1.0
    assert 0 < row["ndcg@10"] <= 1.0
    row = evaluate(store, e, ReverseReranker(), sl, "plumb", SearchConfig(hybrid=True, rerank=True))
    assert row["recall@20"] == 1.0
