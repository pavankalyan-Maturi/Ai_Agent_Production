"""P6.2 answer-correctness judge and its calibration against human labels (Cohen's kappa).

HotpotQA token F1 under-credits correct paraphrases ("1,000" vs "one thousand") and over-credits
partial overlaps, so the gate wants a semantic correctness signal. An LLM judge is only allowed to
gate once it agrees with a human: kappa >= `min_kappa` (gate.toml) on `evals/labels/`. Labels are
written by a person, never generated (SPEC). Until they exist, judge numbers are report-only.
"""

from __future__ import annotations

import csv
import random
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent.model import ChatModel
from agent.reflection import _CITE

JUDGE_SYSTEM = (
    "You grade short answers to multi-hop questions. Compare the candidate with the gold answer. "
    "CORRECT means the candidate states the same fact as the gold (paraphrase, extra precision or "
    "a trailing explanation are fine). INCORRECT means a different, missing, hedged-between-"
    "options or contradictory answer. Ignore [bracketed] citations. Reply with one word: CORRECT "
    "or INCORRECT."
)
LABEL_COLUMNS = ["qid", "question", "gold", "answer", "human_correct", "note"]


def judge_prompt(question: str, gold: str, answer: str) -> str:
    return f"Question: {question}\nGold: {gold}\nCandidate: {_CITE.sub('', answer).strip()}"


def parse_verdict(text: str) -> bool | None:
    m = re.search(r"\b(INCORRECT|CORRECT)\b", text.upper())
    return None if m is None else m.group(1) == "CORRECT"


def judge(model: ChatModel, recs: Sequence[dict[str, Any]]) -> dict[str, bool | None]:
    out: dict[str, bool | None] = {}
    for r in recs:
        d = model.decide(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": judge_prompt(r["question"], r["gold"], r["outcome"]["answer"]),
                },
            ],
            [],
        )
        out[r["qid"]] = parse_verdict(d.text)
    return out


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float:
    n = len(a)
    if n == 0 or n != len(b):
        raise ValueError("need two equal-length, non-empty label lists")
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def kappa_ci(
    a: Sequence[bool], b: Sequence[bool], iters: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """Kappa with a 95% bootstrap interval over items."""
    rng = random.Random(seed)
    idx = range(len(a))
    ks = []
    for _ in range(iters):
        s = rng.choices(idx, k=len(a))
        ks.append(cohen_kappa([a[i] for i in s], [b[i] for i in s]))
    ks.sort()
    return cohen_kappa(a, b), ks[int(0.025 * iters)], ks[int(0.975 * iters)]


def write_label_pool(path: Path, recs: Sequence[dict[str, Any]]) -> None:
    """CSV for a human. No F1 and no judge verdict in it, so neither anchors the label."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, LABEL_COLUMNS)
        w.writeheader()
        for r in recs:
            w.writerow(
                {
                    "qid": r["qid"],
                    "question": r["question"],
                    "gold": r["gold"],
                    "answer": r["outcome"]["answer"],
                    "human_correct": "",
                    "note": "",
                }
            )


def load_labels(path: Path) -> dict[str, bool]:
    """Rows with human_correct 1/0 (or y/n, true/false); blank rows are skipped."""
    if not path.exists():
        return {}
    out = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            v = (row.get("human_correct") or "").strip().lower()
            if v in ("1", "y", "yes", "true", "correct"):
                out[row["qid"]] = True
            elif v in ("0", "n", "no", "false", "incorrect"):
                out[row["qid"]] = False
    return out


def calibrate(
    human: dict[str, bool], verdicts: dict[str, bool | None], f1: dict[str, float]
) -> dict[str, Any]:
    """Judge vs human, and the lexical baseline (F1 >= 0.5) vs human for comparison."""
    ids = sorted(q for q in human if verdicts.get(q) is not None and q in f1)
    if not ids:
        return {"n": 0}
    h = [human[q] for q in ids]
    j = [bool(verdicts[q]) for q in ids]
    lex = [f1[q] >= 0.5 for q in ids]
    k, lo, hi = kappa_ci(h, j)
    kl, llo, lhi = kappa_ci(h, lex)
    return {
        "n": len(ids),
        "judge_kappa": [round(k, 3), round(lo, 3), round(hi, 3)],
        "f1_rule_kappa": [round(kl, 3), round(llo, 3), round(lhi, 3)],
        "human_correct_rate": round(sum(h) / len(h), 3),
        "judge_correct_rate": round(sum(j) / len(j), 3),
    }
