"""SciFact retrieval ablation ladder.

    uv run python -m evals.retrieval.run --dsn postgresql://... --out results/retrieval

Rungs: {naive, contextual} x {dense, hybrid (dense+FTS, RRF), hybrid+rerank}.
Contextual rungs need ANTHROPIC_API_KEY; without it they are reported as NOT MEASURED,
never silently replaced by the mock.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np

from evals.retrieval.metrics import bootstrap_ci, dedupe_docs, ndcg_at, recall_at
from evals.retrieval.scifact import Slice, download, load_slice
from retrieval.chunking import chunk_document
from retrieval.contextual import AnthropicContextualizer, Contextualizer, DiskCache
from retrieval.embedding import FastEmbedEmbedder
from retrieval.pipeline import SearchConfig, index_chunks, search
from retrieval.rerank import FastEmbedReranker
from retrieval.store import PgStore
from retrieval.types import Chunk, Document

# Claude Haiku 5.5 list price per MTok (claude-api skill table, cached 2026-11-03). ESTIMATE.
HAIKU_IN, HAIKU_OUT = 0.10, 0.50

RUNGS = [
    ("dense", SearchConfig()),
    ("hybrid", SearchConfig(hybrid=True)),
    ("hybrid+rerank", SearchConfig(hybrid=True, rerank=True)),
]


def build_chunks(
    sl: Slice, tenant: str, size: int, overlap: int, cz: Contextualizer | None, workers: int
) -> list[Chunk]:
    docs = [Document(tenant, d, text, title) for d, (title, text) in sl.corpus.items()]

    def one(doc: Document) -> list[Chunk]:
        chunks = chunk_document(doc, size, overlap)
        return cz.contextualize(doc.text, chunks) if cz else chunks

    with ThreadPoolExecutor(max_workers=workers if cz else 1) as pool:
        return [c for cs in pool.map(one, docs) for c in cs]


def evaluate(store, embedder, reranker, sl: Slice, tenant: str, cfg: SearchConfig) -> dict:
    r20, n10, lat = [], [], []
    for qid, q in sl.queries.items():
        t = time.perf_counter()
        hits = search(store, embedder, tenant, q, cfg, reranker)
        lat.append((time.perf_counter() - t) * 1000)
        docs = dedupe_docs([h.doc_id for h in hits])
        r20.append(recall_at(docs, sl.qrels[qid], 20))
        n10.append(ndcg_at(docs, sl.qrels[qid], 10))
    return {
        "recall@20": float(np.mean(r20)),
        "recall@20_ci95": bootstrap_ci(r20),
        "failure@20": 1 - float(np.mean(r20)),
        "ndcg@10": float(np.mean(n10)),
        "ndcg@10_ci95": bootstrap_ci(n10),
        "latency_ms_p50": float(np.percentile(lat, 50)),
        "latency_ms_p95": float(np.percentile(lat, 95)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", type=Path, default=Path("results/retrieval"))
    ap.add_argument("--data", type=Path, default=Path(".cache/beir"))
    ap.add_argument("--ctx-cache", type=Path, default=Path(".cache/contexts/scifact.jsonl"))
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--corpus-size", type=int, default=1500)
    ap.add_argument("--chunk-size", type=int, default=96)
    ap.add_argument("--overlap", type=int, default=16)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    sl = load_slice(download(a.data), a.queries, a.corpus_size)
    embedder = FastEmbedEmbedder()
    reranker = FastEmbedReranker()
    store = PgStore(a.dsn, embedder.dim)
    store.migrate()

    variants: dict[str, Contextualizer | None] = {"naive": None}
    cz: AnthropicContextualizer | None = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        cz = AnthropicContextualizer(DiskCache(a.ctx_cache))
        variants["contextual"] = cz

    report: dict = {
        "dataset": "BEIR SciFact test",
        "queries": len(sl.queries),
        "corpus_docs": len(sl.corpus),
        "embedder": embedder.name,
        "reranker": reranker.name,
        "chunk_size_words": a.chunk_size,
        "overlap_words": a.overlap,
        "fts": "Postgres ts_rank_cd (not BM25)",
        "rows": [],
    }
    for variant, czr in variants.items():
        tenant = f"eval-scifact-{variant}"
        store.delete_tenant(tenant)
        t = time.perf_counter()
        chunks = build_chunks(sl, tenant, a.chunk_size, a.overlap, czr, a.workers)
        index_chunks(store, embedder, chunks)
        report[f"index_seconds_{variant}"] = round(time.perf_counter() - t, 1)
        report[f"chunks_{variant}"] = len(chunks)
        for rung, cfg in RUNGS:
            row = {"variant": variant, "retrieval": rung}
            row.update(evaluate(store, embedder, reranker, sl, tenant, cfg))
            report["rows"].append(row)
            print(json.dumps(row))
    if "contextual" not in variants:
        report["rows"] += [
            {
                "variant": "contextual",
                "retrieval": r,
                "status": "NOT MEASURED: no ANTHROPIC_API_KEY",
            }
            for r, _ in RUNGS
        ]
    if cz is not None:
        u = asdict(cz.usage)
        u["est_cost_usd"] = round(
            (u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"])
            * HAIKU_IN
            / 1e6
            + u["output_tokens"] * HAIKU_OUT / 1e6,
            4,
        )
        report["contextual_usage"] = u

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report.json").write_text(json.dumps(report, indent=2))
    md = to_markdown(report)
    (a.out / "report.md").write_text(md)
    print(md)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(md + "\n")


def to_markdown(r: dict) -> str:
    lines = [
        f"### Retrieval ablation: {r['dataset']}, {r['queries']} queries, {r['corpus_docs']} docs",
        f"Embedder `{r['embedder']}`, reranker `{r['reranker']}`, chunks "
        f"{r['chunk_size_words']}w/{r['overlap_words']}w overlap, FTS = {r['fts']}.",
        "",
        "| Variant | Retrieval | Recall@20 [95% CI] | Failure@20 | nDCG@10 [95% CI] | p50 / p95 ms |",
        "|---|---|---|---|---|---|",
    ]
    for row in r["rows"]:
        if "status" in row:
            lines.append(f"| {row['variant']} | {row['retrieval']} | {row['status']} | | | |")
            continue
        lo, hi = row["recall@20_ci95"]
        nlo, nhi = row["ndcg@10_ci95"]
        lines.append(
            f"| {row['variant']} | {row['retrieval']} | {row['recall@20']:.3f} [{lo:.3f}, {hi:.3f}] "
            f"| {row['failure@20']:.3f} | {row['ndcg@10']:.3f} [{nlo:.3f}, {nhi:.3f}] "
            f"| {row['latency_ms_p50']:.0f} / {row['latency_ms_p95']:.0f} |"
        )
    if u := r.get("contextual_usage"):
        lines.append("")
        lines.append(f"Contextual usage: {json.dumps(u)} (cost is an ESTIMATE at list price)")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
