"""BEIR SciFact loader with a deterministic slice (queries + relevant docs + seeded distractors)."""

from __future__ import annotations

import csv
import io
import json
import random
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"


@dataclass(frozen=True)
class Slice:
    corpus: dict[str, tuple[str, str]]  # doc_id -> (title, text)
    queries: dict[str, str]
    qrels: dict[str, set[str]]


def download(cache_dir: Path) -> Path:
    root = cache_dir / "scifact"
    if not (root / "corpus.jsonl").exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(URL, timeout=120) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extractall(cache_dir)
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
