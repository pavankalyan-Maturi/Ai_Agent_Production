import gzip
import json
from pathlib import Path

import pytest

from evals.retrieval import scifact


def test_pick_prefers_shortest_match() -> None:
    paths = ["corpus/x.parquet", "data/corpus.jsonl.gz", "corpus.jsonl.gz", "queries.jsonl"]
    assert scifact._pick(paths, "corpus", (".jsonl", ".jsonl.gz")) == "corpus.jsonl.gz"
    assert scifact._pick(paths, "qrels", (".tsv",)) is None


def test_download_falls_through_to_second_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = b'{"_id": "1", "title": "t", "text": "x"}\n'
    queries = b'{"_id": "9", "text": "q"}\n'
    qrels = b'{"query-id": "9", "corpus-id": "1", "score": 1}\n'
    tree = [
        {"type": "file", "path": p}
        for p in ("corpus.jsonl", "queries.jsonl.gz", "qrels/test.jsonl")
    ]

    def fake_fetch(url: str, timeout: float = 120) -> bytes:
        if "BeIR" in url:
            raise OSError("404")
        if "/api/" in url:
            return json.dumps(tree).encode()
        body = {
            "corpus.jsonl": corpus,
            "queries.jsonl.gz": gzip.compress(queries),
            "test.jsonl": qrels,
        }[url.rsplit("/", 1)[-1]]
        return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body

    monkeypatch.setattr(scifact, "_fetch", fake_fetch)
    root = scifact.download(tmp_path)
    sl = scifact.load_slice(root, n_queries=5, corpus_size=5)
    assert sl.qrels == {"9": {"1"}}
    assert sl.queries == {"9": "q"}
    assert sl.corpus == {"1": ("t", "x")}
