"""BEIR SciFact loader with a deterministic slice (queries + relevant docs + seeded distractors)."""

from __future__ import annotations

import csv
import gzip
import io
import json
import random
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

HF = "https://huggingface.co"
# Hugging Face mirrors, tried in order. File layouts differ between mirrors and have moved
# before, so paths are discovered from the repo tree instead of hard-coded.
HF_SOURCES = [
    ("BeIR/scifact", "BeIR/scifact-qrels"),
    ("mteb/scifact", "mteb/scifact"),
]
# Original BEIR host; slow and sometimes unreachable from CI runners, so only a fallback.
UKP_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
WANT = ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv")


def _fetch(url: str, timeout: float = 120) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(url)
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        data: bytes = r.read()
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def _tree(repo: str) -> list[str]:
    raw = _fetch(f"{HF}/api/datasets/{repo}/tree/main?recursive=true", timeout=30)
    return [e["path"] for e in json.loads(raw) if e.get("type") == "file"]


def _pick(paths: list[str], stem: str, exts: tuple[str, ...]) -> str | None:
    """Shortest path whose basename is stem + one of exts (e.g. corpus.jsonl.gz)."""
    hits = [p for p in paths if any(p.rsplit("/", 1)[-1] == stem + e for e in exts)]
    return min(hits, key=len) if hits else None


def _qrels_jsonl_to_tsv(data: bytes) -> bytes:
    out = ["query-id\tcorpus-id\tscore"]
    for line in data.decode().splitlines():
        if line.strip():
            r = json.loads(line)
            out.append(f"{r['query-id']}\t{r['corpus-id']}\t{r['score']}")
    return ("\n".join(out) + "\n").encode()


def _from_hf(docs_repo: str, qrels_repo: str) -> dict[str, bytes]:
    docs = _tree(docs_repo)
    qtree = docs if qrels_repo == docs_repo else _tree(qrels_repo)
    corpus = _pick(docs, "corpus", (".jsonl", ".jsonl.gz"))
    queries = _pick(docs, "queries", (".jsonl", ".jsonl.gz"))
    qrels = _pick(qtree, "test", (".tsv", ".jsonl", ".tsv.gz", ".jsonl.gz"))
    if not (corpus and queries and qrels):
        raise FileNotFoundError(f"{docs_repo}: corpus={corpus} queries={queries} qrels={qrels}")
    base = f"{HF}/datasets/{{}}/resolve/main/{{}}"
    q = _fetch(base.format(qrels_repo, qrels))
    if ".jsonl" in qrels:
        q = _qrels_jsonl_to_tsv(q)
    return {
        "corpus.jsonl": _fetch(base.format(docs_repo, corpus)),
        "queries.jsonl": _fetch(base.format(docs_repo, queries)),
        "qrels/test.tsv": q,
    }


@dataclass(frozen=True)
class Slice:
    corpus: dict[str, tuple[str, str]]  # doc_id -> (title, text)
    queries: dict[str, str]
    qrels: dict[str, set[str]]


def download(cache_dir: Path) -> Path:
    root = cache_dir / "scifact"
    if all((root / rel).exists() for rel in WANT):
        return root
    errors = []
    for docs_repo, qrels_repo in HF_SOURCES:
        try:
            files = _from_hf(docs_repo, qrels_repo)
        except (OSError, ValueError, KeyError) as e:
            errors.append(f"{docs_repo}: {e!r}")
            continue
        for rel, data in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        print(f"scifact: downloaded from hf:{docs_repo}", flush=True)
        return root
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(_fetch(UKP_URL))).extractall(cache_dir)
    except OSError as e:
        raise RuntimeError("SciFact download failed: " + "; ".join([*errors, repr(e)])) from e
    return root


def load_slice(root: Path, n_queries: int, corpus_size: int, seed: int = 13) -> Slice:
    qrels: dict[str, set[str]] = {}
    with (root / "qrels" / "test.tsv").open() as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if int(row["score"]) > 0:
                qrels.setdefault(row["query-id"], set()).add(row["corpus-id"])
    qids = sorted(qrels, key=int)[:n_queries]
    qrels = {q: qrels[q] for q in qids}

    queries = {}
    with (root / "queries.jsonl").open() as f:
        for line in f:
            rec = json.loads(line)
            if rec["_id"] in qrels:
                queries[rec["_id"]] = rec["text"]

    full: dict[str, tuple[str, str]] = {}
    with (root / "corpus.jsonl").open() as f:
        for line in f:
            rec = json.loads(line)
            full[rec["_id"]] = (rec.get("title", ""), rec["text"])

    relevant = set().union(*qrels.values())
    others = sorted(set(full) - relevant)
    random.Random(seed).shuffle(others)
    keep = sorted(relevant) + others[: max(0, corpus_size - len(relevant))]
    return Slice({d: full[d] for d in keep}, queries, qrels)
