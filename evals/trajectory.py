"""P6.1 trajectory metrics and P6.3 rule-based MAST tagging over recorded agent runs.

Input is `results/agent/trajectories.jsonl` written by `evals.agent.run` (shipped config, memory
off). Everything here is deterministic and free: it reads messages, it never calls a model.

MAST (Cemri et al., 2025, "Why Do Multi-Agent LLM Systems Fail?") defines 14 failure modes in 3
categories. This agent is a single planner/executor, so the inter-agent modes (FM-2.x) mostly do
not apply; we tag the 6 that can be decided from the trace alone. Tags are an approximation of
the paper's LLM-annotator protocol and have not been checked against human tags (UNVERIFIED).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent.model import Message
from agent.reflection import _CITE, citations_grounded, seen_doc_ids

FAIL_F1 = 0.5  # below this (and not judged correct) a run counts as a failure for MAST tagging
SHORT_ANSWER_WORDS = 12  # the task asks for a short phrase + [doc_id]s

MAST = {
    "FM-1.1": "disobey task specification (answer not a short phrase or no citation)",
    "FM-1.3": "step repetition (same tool call with the same args)",
    "FM-1.5": "unaware of termination conditions (ran into the step/token/$ budget)",
    "FM-3.1": "premature termination (answered before seeing every supporting doc)",
    "FM-3.2": "no or incomplete verification (cited docs are not the supporting docs)",
    "FM-3.3": "incorrect verification (had the evidence, passed the verifier, still wrong)",
}


@dataclass(frozen=True)
class TrajMetrics:
    qid: str
    tool_calls: int
    searches: int
    fetches: int
    redundant_calls: int
    evidence_recall: float  # supporting titles the agent saw / supporting titles
    citation_precision: float  # cited ids that are supporting / cited ids
    grounded: bool  # every cited id came back from a tool in this run
    degraded: bool
    answer_words: int


def _calls(messages: Sequence[Message]) -> list[tuple[str, str]]:
    out = []
    for m in messages:
        if m["role"] == "assistant":
            for c in m.get("tool_calls") or ():
                out.append((c["name"], json.dumps(c.get("args", {}), sort_keys=True)))
    return out


def _answer_body(answer: str) -> str:
    return _CITE.sub("", answer).strip()


def metrics(rec: dict[str, Any]) -> TrajMetrics:
    msgs, o = rec["messages"], rec["outcome"]
    sup = set(rec.get("supporting") or ())
    calls = _calls(msgs)
    seen = seen_doc_ids(msgs)
    cited = set(_CITE.findall(o["answer"]))
    return TrajMetrics(
        qid=rec["qid"],
        tool_calls=len(calls),
        searches=sum(n == "search" for n, _ in calls),
        fetches=sum(n == "fetch_doc" for n, _ in calls),
        redundant_calls=len(calls) - len(set(calls)),
        evidence_recall=len(sup & seen) / len(sup) if sup else 0.0,
        citation_precision=len(cited & sup) / len(cited) if cited else 0.0,
        grounded=citations_grounded(msgs, o["answer"]) is None,
        degraded=o["status"] == "degraded",
        answer_words=len(_answer_body(o["answer"]).split()),
    )


def is_failure(rec: dict[str, Any], judged_correct: bool | None = None) -> bool:
    if judged_correct is not None:
        return not judged_correct
    return float(rec["outcome"]["f1"]) < FAIL_F1


def mast_tags(rec: dict[str, Any], m: TrajMetrics) -> list[str]:
    """Multi-label: a failed run can show several modes. Call only on failures."""
    tags = []
    if m.answer_words > SHORT_ANSWER_WORDS or not _CITE.search(rec["outcome"]["answer"]):
        tags.append("FM-1.1")
    if m.redundant_calls:
        tags.append("FM-1.3")
    if m.degraded:
        tags.append("FM-1.5")
    if not m.degraded and m.evidence_recall < 1.0:
        tags.append("FM-3.1")
    if m.citation_precision < 1.0:
        tags.append("FM-3.2")
    if m.evidence_recall == 1.0 and m.grounded and not m.degraded:
        tags.append("FM-3.3")
    return tags or ["untagged"]


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize(
    recs: Iterable[dict[str, Any]], judged: dict[str, bool] | None = None
) -> dict[str, Any]:
    recs = list(recs)
    ms = [metrics(r) for r in recs]
    hist: Counter[str] = Counter()
    failures = 0
    for r, m in zip(recs, ms, strict=True):
        if is_failure(r, (judged or {}).get(r["qid"])):
            failures += 1
            hist.update(mast_tags(r, m))

    def mean(f: str) -> float:
        return round(statistics.fmean(float(getattr(m, f)) for m in ms), 3) if ms else 0.0

    return {
        "n": len(ms),
        "tool_calls_mean": mean("tool_calls"),
        "redundant_call_rate": round(
            sum(m.redundant_calls for m in ms) / max(1, sum(m.tool_calls for m in ms)), 3
        ),
        "evidence_recall": mean("evidence_recall"),
        "citation_precision": mean("citation_precision"),
        "grounded_rate": mean("grounded"),
        "degraded_rate": mean("degraded"),
        "failures": failures,
        "mast_histogram": dict(sorted(hist.items())),
        "per_question": [asdict(m) for m in ms],
    }


def to_markdown(s: dict[str, Any]) -> str:
    lines = [
        f"Trajectories: {s['n']} (shipped config). Tool calls/q {s['tool_calls_mean']}, "
        f"redundant-call rate {s['redundant_call_rate']}, evidence recall "
        f"{s['evidence_recall']}, citation precision {s['citation_precision']}, grounded "
        f"{s['grounded_rate']}, degraded {s['degraded_rate']}.",
        "",
        f"MAST failure histogram (rule-based, multi-label, {s['failures']} failed runs):",
        "",
        "| Mode | Runs | Meaning |",
        "|---|---|---|",
    ]
    for k, v in sorted(s["mast_histogram"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} | {MAST.get(k, 'no rule matched')} |")
    return "\n".join(lines)
