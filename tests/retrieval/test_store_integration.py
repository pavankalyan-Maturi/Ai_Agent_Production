"""Needs Postgres + pgvector >= 0.8. Set PAP_TEST_DATABASE_URL (CI job `integration` does)."""

import os

import numpy as np
import psycopg
import pytest

from retrieval.chunking import chunk_document
from retrieval.embedding import HashEmbedder
from retrieval.pipeline import SearchConfig, index_documents, search
from retrieval.store import PgStore
from retrieval.types import Chunk, Document

DSN = os.environ.get("PAP_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="PAP_TEST_DATABASE_URL not set"),
]
DIM = 384


@pytest.fixture(scope="module")
def store() -> PgStore:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS chunks")
    s = PgStore(DSN, DIM)
    s.migrate()
    return s


DOCS = [
    ("d1", "Vitamin D supplementation reduces fracture risk in elderly women."),
    ("d2", "Graph neural networks learn representations of molecules."),
    ("d3", "Statins lower LDL cholesterol and cardiovascular events."),
]


def test_tenant_isolation_and_idempotent_upsert(store: PgStore) -> None:
    e = HashEmbedder(DIM)
    for tenant in ("acme", "globex"):
        docs = [Document(tenant, d, t) for d, t in DOCS]
        index_documents(store, e, docs)
        index_documents(store, e, docs)  # redelivery: no duplicates
    assert store.count("acme") == 3
    hits = search(store, e, "acme", "vitamin d fractures", SearchConfig(hybrid=True, top_k=3))
    assert hits[0].doc_id == "d1"
    assert {h.chunk_id for h in hits}.isdisjoint(
        {c.chunk_id for d, t in DOCS for c in chunk_document(Document("globex", d, t))}
    )
    assert search(store, e, "nobody", "vitamin", SearchConfig(hybrid=True)) == []


def test_fts_matches_any_query_term(store: PgStore) -> None:
    hits = store.fts("acme", "cholesterol quantum", 5)
    assert [h.doc_id for h in hits] == ["d3"]


def test_filtered_ann_recall_needs_iterative_scan(store: PgStore) -> None:
    """A 1%-selective tenant filter on HNSW with ef_search=40 starves without iterative scans."""
    rng = np.random.default_rng(0)
    big, small = 20_000, 200
    vecs = rng.standard_normal((big + small, DIM)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    chunks = [
        Chunk("big" if i < big else "small", f"doc{i}", f"c{i}", 0, f"text {i}")
        for i in range(big + small)
    ]
    store.upsert(chunks, list(vecs))

    k, recalls_off, recalls_on, counts_off = 10, [], [], []
    for q in rng.standard_normal((20, DIM)).astype(np.float32):
        q /= np.linalg.norm(q)
        truth = {h.chunk_id for h in store.dense("small", q, k, exact=True)}
        off = store.dense("small", q, k, ef_search=40, iterative_scan="off")
        on = store.dense("small", q, k, ef_search=40, iterative_scan="relaxed_order")
        counts_off.append(len(off))
        recalls_off.append(len({h.chunk_id for h in off} & truth) / k)
        recalls_on.append(len({h.chunk_id for h in on} & truth) / k)
        assert len(on) == k
        assert [h.score for h in on] == sorted((h.score for h in on), reverse=True)

    print(
        f"recall@{k} off={np.mean(recalls_off):.3f} on={np.mean(recalls_on):.3f} "
        f"mean_rows_off={np.mean(counts_off):.1f}"
    )
    # Random 384-d vectors are a hard case for HNSW even unfiltered; the claim under test is
    # that the filter no longer starves the scan (locally: off 0.015, on 0.845).
    assert np.mean(recalls_on) >= 0.75
    assert np.mean(recalls_off) < 0.2
