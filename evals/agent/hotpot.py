"""HotpotQA (distractor, validation) slice: the first n questions, in dataset order.

Rows come from the Hugging Face datasets-server JSON API (no parquet dependency); the original
CMU dev file is the fallback. Each question has 10 paragraphs (2 gold + 8 distractors); the
slice's paragraphs, deduplicated by title, form one tenant's corpus.
"""

from __future__ import annotations

import json
import re
import string
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROWS = "https://datasets-server.huggingface.co/rows"
DATASETS = ["hotpotqa/hotpot_qa", "hotpot_qa"]
CMU = "http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json"


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    answer: str
    type: str  # bridge | comparison
    paragraphs: tuple[tuple[str, str], ...]  # (title, text)


def _get(url: str, timeout: float = 60) -> Any:
    if not url.startswith(("https://", "http://curtis.ml.cmu.edu/")):
        raise ValueError(url)
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read())


def parse_hf_row(row: dict[str, Any]) -> Question:
    ctx = row["context"]
    paras = tuple(
        (t, "".join(s).strip()) for t, s in zip(ctx["title"], ctx["sentences"], strict=True)
    )
    return Question(row["id"], row["question"], row["answer"], row.get("type", ""), paras)


def parse_cmu_row(row: dict[str, Any]) -> Question:
    paras = tuple((t, "".join(s).strip()) for t, s in row["context"])
    return Question(row["_id"], row["question"], row["answer"], row.get("type", ""), paras)


def _from_hf(n: int) -> list[Question]:
    errors = []
    for ds in DATASETS:
        try:
            out: list[Question] = []
            while len(out) < n:
                q = urllib.parse.urlencode(
                    {
                        "dataset": ds,
                        "config": "distractor",
                        "split": "validation",
                        "offset": len(out),
                        "length": min(100, n - len(out)),
                    }
                )
                rows = _get(f"{ROWS}?{q}")["rows"]
                if not rows:
                    break
                out += [parse_hf_row(r["row"]) for r in rows]
            return out[:n]
        except Exception as e:  # try the next source
            errors.append(f"{ds}: {type(e).__name__}: {e}")
    raise RuntimeError("; ".join(errors))


def load(n: int, cache: Path) -> list[Question]:
    path = cache / f"hotpot_distractor_val_{n}.json"
    if path.exists():
        return [
            Question(**{**r, "paragraphs": tuple(map(tuple, r["paragraphs"]))})
            for r in json.loads(path.read_text())
        ]
    try:
        qs = _from_hf(n)
    except RuntimeError as hf_err:
        try:
            qs = [parse_cmu_row(r) for r in _get(CMU, timeout=300)[:n]]
        except Exception as e:
            raise RuntimeError(f"HotpotQA download failed: {hf_err}; CMU: {e}") from e
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([q.__dict__ for q in qs]))
    return qs


def corpus(qs: list[Question]) -> dict[str, str]:
    docs: dict[str, str] = {}
    for q in qs:
        for title, text in q.paragraphs:
            docs.setdefault(title, f"{title}: {text}")
    return docs


# ---- SQuAD-style answer metrics (the official HotpotQA script uses the same normalisation) ----

_CITES = re.compile(r"\[[^\[\]]*\]")


def normalize(s: str) -> str:
    s = _CITES.sub(" ", s).lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def exact_match(pred: str, gold: str) -> float:
    return float(normalize(pred) == normalize(gold))


def f1(pred: str, gold: str) -> float:
    p, g = normalize(pred).split(), normalize(gold).split()
    if not p or not g:
        return float(p == g)
    common = Counter(p) & Counter(g)
    same = sum(common.values())
    if same == 0:
        return 0.0
    prec, rec = same / len(p), same / len(g)
    return 2 * prec * rec / (prec + rec)
