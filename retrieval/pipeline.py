"""Index and search pipelines composed from the retrieval parts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from retrieval.chunking import chunk_document
from retrieval.contextual import Contextualizer
from retrieval.embedding import Embedder
from retrieval.fusion import rrf
from retrieval.rerank import Reranker
from retrieval.store import IterativeScan, PgStore
from retrieval.types import Chunk, Document, Hit


@dataclass(frozen=True)
class SearchConfig:
    hybrid: bool = False
    rerank: bool = False
    candidates: int = 50  # per retriever, before fusion / rerank
    top_k: int = 20
    rrf_k: int = 60
    iterative_scan: IterativeScan = "relaxed_order"
    ef_search: int = 100


def index_chunks(store: PgStore, embedder: Embedder, chunks: list[Chunk], batch: int = 256) -> int:
    n = 0
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        n += store.upsert(part, embedder.embed_documents([c.indexed_text for c in part]))
    return n


def index_documents(
    store: PgStore,
    embedder: Embedder,
    docs: Iterable[Document],
    *,
    contextualizer: Contextualizer | None = None,
    chunk_size: int = 96,
    overlap: int = 16,
    batch: int = 256,
) -> int:
    pending = []
    n = 0
    for doc in docs:
        chunks = chunk_document(doc, chunk_size, overlap)
        if contextualizer is not None:
            chunks = contextualizer.contextualize(doc.text, chunks)
        pending.extend(chunks)
        if len(pending) >= batch:
            n += store.upsert(pending, embedder.embed_documents([c.indexed_text for c in pending]))
            pending = []
    if pending:
        n += store.upsert(pending, embedder.embed_documents([c.indexed_text for c in pending]))
    return n


def search(
    store: PgStore,
    embedder: Embedder,
    tenant_id: str,
    query: str,
    cfg: SearchConfig,
    reranker: Reranker | None = None,
) -> list[Hit]:
    dense = store.dense(
        tenant_id,
        embedder.embed_query(query),
        cfg.candidates,
        ef_search=cfg.ef_search,
        iterative_scan=cfg.iterative_scan,
    )
    hits = (
        rrf([dense, store.fts(tenant_id, query, cfg.candidates)], k=cfg.rrf_k)
        if cfg.hybrid
        else dense
    )
    if cfg.rerank:
        if reranker is None:
            raise ValueError("rerank=True needs a reranker")
        return reranker.rerank(query, hits[: cfg.candidates], cfg.top_k)
    return hits[: cfg.top_k]
