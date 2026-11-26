"""Cross-encoder reranking behind a small interface."""

from __future__ import annotations

from typing import Protocol

from retrieval.types import Hit


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]: ...


class FastEmbedReranker:
    """Small CPU cross-encoder (ONNX). ~22M params: runs in CI, unlike 8B reasoning rerankers."""

    def __init__(self, model: str = "Xenova/ms-marco-MiniLM-L-6-v2") -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._m = TextCrossEncoder(model_name=model)
        self.name = model

    def rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        if not hits:
            return []
        scores = list(self._m.rerank(query, [h.content for h in hits]))
        ranked = sorted(zip(hits, scores, strict=True), key=lambda p: -p[1])
        return [Hit(h.chunk_id, h.doc_id, float(s), h.content) for h, s in ranked[:top_k]]
