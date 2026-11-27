"""Tool-count scaling eval for P3.3 (RAG-MCP tool retrieval).

    uv run python -m evals.tools.run --embedder hash --k 5
    uv run python -m evals.tools.run --embedder fastembed --k 5   # needs model download

For n tools in the catalog, compares offering all tools vs the top-k selector:
- recall@k: the gold tool for the query is among the offered tools
- offered spec tokens per model call (JSON spec size / 4, an estimate, not a tokenizer)
- selection latency per question

It measures whether the right tool is offered and what that saves. It does NOT measure
end-to-end task success with a real model (NOT MEASURED; that costs model calls).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path

from agent.model import ToolSpec
from agent.tool_retrieval import ToolSelector
from evals.tools.catalog import catalog
from retrieval.embedding import Embedder, FastEmbedEmbedder, HashEmbedder

SIZES = (10, 25, 50, 100, 163)


def spec_tokens(specs: list[ToolSpec]) -> int:
    return sum(len(json.dumps(asdict(s))) for s in specs) // 4


def run(embedder: Embedder, k: int, sizes: tuple[int, ...] = SIZES) -> list[dict[str, float]]:
    rows = []
    for n in sizes:
        specs, queries = catalog(n)
        sel = ToolSelector(embedder, k=k)
        t0 = time.perf_counter()
        sel.index(specs)
        index_ms = (time.perf_counter() - t0) * 1000
        hits, lat, offered = 0, [], []
        for q, gold in queries:
            t0 = time.perf_counter()
            chosen = sel.select(q, specs)
            lat.append((time.perf_counter() - t0) * 1000)
            hits += any(s.name == gold for s in chosen)
            offered.append(spec_tokens(chosen))
        rows.append(
            {
                "n_tools": n,
                "queries": len(queries),
                "recall_at_k": hits / len(queries),
                "tokens_all": spec_tokens(specs),
                "tokens_selected": statistics.mean(offered),
                "select_ms_p50": statistics.median(lat),
                "index_ms": index_ms,
            }
        )
    return rows


def table(rows: list[dict[str, float]], k: int, embedder: str) -> str:
    out = [
        f"embedder={embedder} k={k}",
        "| tools | queries | recall@k | spec tokens, all | spec tokens, top-k | select p50 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| {r['n_tools']} | {r['queries']} | {r['recall_at_k']:.3f} | {r['tokens_all']} "
            f"| {r['tokens_selected']:.0f} | {r['select_ms_p50']:.2f} ms |"
        )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", choices=["hash", "fastembed"], default="hash")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    emb: Embedder = HashEmbedder(384) if a.embedder == "hash" else FastEmbedEmbedder()
    rows = run(emb, a.k)
    print(table(rows, a.k, emb.name))
    if a.out:
        a.out.write_text(json.dumps({"embedder": emb.name, "k": a.k, "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
