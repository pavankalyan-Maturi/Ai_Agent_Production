"""Doc-level IR metrics with bootstrap confidence intervals."""

from __future__ import annotations

import math

import numpy as np


def dedupe_docs(doc_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for d in doc_ids:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def recall_at(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def ndcg_at(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, d in enumerate(ranked[:k]) if d in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / ideal if ideal else 0.0


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% percentile bootstrap over queries."""
    a = np.asarray(values)
    idx = np.random.default_rng(seed).integers(0, len(a), size=(n, len(a)))
    means = a[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
