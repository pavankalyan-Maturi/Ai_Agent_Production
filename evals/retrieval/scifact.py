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

HF = "https://huggingface.co/datasets/BeIR"
HF_FILES = {
    "corpus.jsonl": f"{HF}/scifact/resolve/main/corpus.jsonl.gz",
    "queries.jsonl": f"{HF}/scifact/resolve/main/queries.jsonl.gz",
    "qrels/test.tsv": f"{HF}/scifact-qrels/resolve/main/test.tsv",
}
# Original BEIR host; slow and sometimes unreachable from CI runners, so only a fallback.
UKP_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"


def _fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(url)
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310
        data: bytes = r.read()
    return gzip.decompress(data) if url.endswith(".gz") else data


@dataclass(frozen=True)
class Slice:
    corpus: dict[str, tuple[str, str]]  # doc_id -> (title, text)
    queries: dict[str, str]
    qrels: dict[str, set[str]]


def download(cache_dir: Path) -> Path:
    root = cache_dir / "scifact"
    if (root / "corpus.jsonl").exists():
        return root
    try:
        for rel, url in HF_FILES.items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_fetch(url))
    except OSError:
        cache_dir.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(_fetch(UKP_URL))).extractall(cache_dir)
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
