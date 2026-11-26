"""Reciprocal rank fusion (Cormack et al., 2009)."""

from __future__ import annotations

from retrieval.types import Hit


def rrf(ranked_lists: list[list[Hit]], k: int = 60, top_k: int | None = None) -> list[Hit]:
    """Score = sum over lists of 1 / (k + rank).

    Rank-only, so dense and FTS scores need no calibration against each other.
    """
    scores: dict[str, float] = {}
    first: dict[str, Hit] = {}
    for hits in ranked_lists:
        for rank, h in enumerate(hits, start=1):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1.0 / (k + rank)
            first.setdefault(h.chunk_id, h)
    order = sorted(scores, key=lambda cid: (-scores[cid], cid))
    out = [Hit(cid, first[cid].doc_id, scores[cid], first[cid].content) for cid in order]
    return out[:top_k] if top_k else out
