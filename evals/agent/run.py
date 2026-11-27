"""P2.5 ablation on a HotpotQA slice: experience memory off / on (ungated) / gated.

All arms share one agent config (search + fetch_doc, compaction, citation-grounded reflection).
Questions stream in dataset order, in batches; memory written after batch b is recalled from
batch b+1 on. The grade (answer F1 vs gold) is the external signal that labels each trajectory
for distillation and scores gate replays.

usage: uv run python -m evals.agent.run --dsn postgresql://... [--questions 100] [--max-usd 2]
Needs ANTHROPIC_API_KEY. Without it, every row is NOT MEASURED and the job exits 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from agent.compaction import CompactionPolicy
from agent.graph import build_graph
from agent.memory import (
    AdmissionGate,
    InMemoryBank,
    MemoryItem,
    Recall,
    RunResult,
    admit_ungated,
    llm_distiller,
)
from agent.model import AnthropicModel, ChatModel, Decision, Message, ToolCall, ToolSpec, cost_usd
from agent.reflection import citations_grounded
from agent.state import Budget
from agent.tools import fetch_doc_tool, search_tool
from evals.agent.hotpot import Question, corpus, exact_match, f1, load
from retrieval.embedding import Embedder, Vec
from retrieval.pipeline import SearchConfig, index_documents
from retrieval.store import PgStore
from retrieval.types import Document

MODEL = "claude-haiku-5-5"
HAIKU_EXTRA = {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
TENANT = "eval-hotpot"
SUCCESS_F1 = 0.8  # distil a strategy at or above this F1
FAILURE_F1 = 0.2  # distil a pitfall at or below; in between the label is too noisy (run 1, ADR 005)
RECALL_K, RECALL_MIN_SIM = 2, 0.6
INSTRUCTION = (
    "\n\nReply with only the short answer phrase (a name, date, number or yes/no), "
    "followed by the [doc_id]s it comes from."
)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Spend:
    """Real money spent (cache hits are free). Shared by all threads; hard cap."""

    cap_usd: float
    usd: float = 0.0
    calls: int = 0
    cached: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def charge(self, usd: float) -> None:
        with self._lock:
            self.usd += usd
            self.calls += 1
            if self.usd > self.cap_usd:
                raise BudgetExceeded(f"eval spend ${self.usd:.2f} > cap ${self.cap_usd:.2f}")


class CachedModel:
    """Content-addressed response cache: re-running unchanged code costs nothing."""

    def __init__(self, inner: ChatModel, path: Path, spend: Spend) -> None:
        self.inner, self.path, self.spend, self.name = inner, path, spend, inner.name
        self._lock = threading.Lock()
        self._d: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                self._d[rec["k"]] = rec["v"]

    def _key(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> str:
        view = [{k: v for k, v in m.items() if k != "raw"} for m in messages]
        raw = json.dumps([self.name, view, [t.name for t in tools]], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def decide(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Decision:
        k = self._key(messages, tools)
        hit = self._d.get(k)
        if hit is None:
            if self.spend.usd > self.spend.cap_usd:
                raise BudgetExceeded("cap reached")
            d = self.inner.decide(messages, tools)
            hit = {
                "text": d.text,
                "tool_calls": [[c.id, c.name, c.args] for c in d.tool_calls],
                "in": d.input_tokens,
                "out": d.output_tokens,
            }
            with self._lock:
                self._d[k] = hit
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(json.dumps({"k": k, "v": hit}) + "\n")
            self.spend.charge(cost_usd(self.name, d.input_tokens, d.output_tokens))
        else:
            with self.spend._lock:
                self.spend.cached += 1
        # raw is dropped: replayed turns are rebuilt from tool_calls (thinking is disabled here).
        return Decision(
            text=hit["text"],
            tool_calls=tuple(ToolCall(*c) for c in hit["tool_calls"]),
            input_tokens=hit["in"],
            output_tokens=hit["out"],
        )


class LockedEmbedder:
    """Serialises calls into the ONNX model; worker threads share one instance."""

    def __init__(self, inner: Embedder) -> None:
        self.inner, self.dim, self.name = inner, inner.dim, inner.name
        self._lock = threading.Lock()

    def embed_query(self, text: str) -> Vec:
        with self._lock:
            return self.inner.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[Vec]:
        with self._lock:
            return self.inner.embed_documents(texts)


@dataclass
class Outcome:
    qid: str
    em: float
    f1: float
    steps: int
    tokens: int
    usd: float
    status: str
    reflections: int
    recalled: int
    context_tokens: list[int]
    answer: str


def make_runner(model: ChatModel, store: PgStore, emb: Embedder) -> Callable[..., Any]:
    tools = [
        search_tool(store, emb, SearchConfig(hybrid=True, top_k=5)),
        fetch_doc_tool(store, 3000),
    ]
    budget = Budget(max_steps=8, max_tokens=60_000, max_usd=0.05)
    pol = CompactionPolicy(tool_result_chars=3000, threshold_tokens=5000, keep_last=4)

    def run(
        q: Question, recall: Callable[[str, str], list[MemoryItem]] | None
    ) -> tuple[Outcome, list[Message]]:
        g = build_graph(
            model,
            tools,
            budget,
            compaction=pol,
            recall=recall,
            verifiers=[citations_grounded],
            max_reflections=1,
        )
        s = g.invoke({"tenant_id": TENANT, "question": q.question + INSTRUCTION})
        ans = s.get("answer", "")
        return Outcome(
            q.id,
            exact_match(ans, q.answer),
            f1(ans, q.answer),
            s["steps"],
            s["tokens"],
            s["usd"],
            s["status"],
            s.get("reflections", 0),
            len(s.get("memory_ids", [])),
            s.get("context_tokens", []),
            ans[:200],
        ), s["messages"]

    return run


def arm(
    name: str,
    qs: list[Question],
    run: Callable[..., Any],
    distill: Any,
    emb: Embedder,
    batch: int,
    workers: int,
) -> dict[str, Any]:
    bank = InMemoryBank()
    gate = AdmissionGate(bank, emb) if name == "gated" else None
    recall = Recall(bank, emb, k=RECALL_K, min_sim=RECALL_MIN_SIM) if name != "off" else None
    outs: list[Outcome] = []
    seen: list[tuple[Question, Outcome, np.ndarray]] = []
    pool = ThreadPoolExecutor(workers)
    for b in range(0, len(qs), batch):
        chunk = qs[b : b + batch]
        results = list(pool.map(lambda q: run(q, recall), chunk))
        outs += [r[0] for r in results]
        if name == "off":
            continue
        seen += [
            (q, r[0], unit(emb.embed_query(q.question)))
            for q, r in zip(chunk, results, strict=True)
        ]

        def learn(
            qr: tuple[Question, tuple[Outcome, list[Message]]],
            seen: list[tuple[Question, Outcome, np.ndarray]] = seen,
        ) -> None:
            q, (o, msgs) = qr
            if FAILURE_F1 < o.f1 < SUCCESS_F1:
                return
            label = "success" if o.f1 >= SUCCESS_F1 else "failure"
            items, _, _ = distill(q.question, msgs, label)
            for it in items:
                if gate is None:
                    admit_ungated(bank, emb, TENANT, it)
                    continue
                # Transfer test: replay the most similar *other* question seen so far with only
                # this item recalled, against that question's own run without it. Replaying the
                # source task (run 1) admitted items that only fit their own question.
                nb = nearest_other(seen, q)
                if nb is None:
                    continue

                def replay(item: MemoryItem, nq: Question = nb[0]) -> RunResult:
                    r, _ = run(nq, lambda _t, _q: [item])
                    return RunResult(r.f1, r.steps)

                gate.admit(TENANT, it, q.answer, RunResult(nb[1].f1, nb[1].steps), replay)

        list(pool.map(learn, zip(chunk, results, strict=True)))
        print(f"[{name}] {len(outs)}/{len(qs)} memory={bank.count(TENANT)}", flush=True)
    pool.shutdown()
    return {
        "outcomes": outs,
        "memory": bank.count(TENANT),
        "gate": dict(gate.stats) if gate else {},
    }


def unit(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    return a / (np.linalg.norm(a) or 1.0)


def nearest_other(
    seen: list[tuple[Question, Outcome, np.ndarray]], q: Question
) -> tuple[Question, Outcome] | None:
    src = next((v for sq, _, v in seen if sq.id == q.id), None)
    others = [(sq, so, v) for sq, so, v in seen if sq.id != q.id]
    if src is None or not others:
        return None
    sq, so, _ = max(others, key=lambda t: float(t[2] @ src))
    return sq, so


def paired_ci(
    a: list[float], b: list[float], iters: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """Mean of (b - a) with a 95% paired-bootstrap interval."""
    d = [y - x for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(iters))
    return statistics.fmean(d), means[int(0.025 * iters)], means[int(0.975 * iters)]


def summarize(name: str, r: dict[str, Any], base: list[Outcome] | None) -> dict[str, Any]:
    o: list[Outcome] = r["outcomes"]
    half = len(o) // 2
    row = {
        "arm": name,
        "n": len(o),
        "em": round(statistics.fmean(x.em for x in o), 3),
        "f1": round(statistics.fmean(x.f1 for x in o), 3),
        "f1_second_half": round(statistics.fmean(x.f1 for x in o[half:]), 3),
        "steps_mean": round(statistics.fmean(x.steps for x in o), 2),
        "tokens_per_q": round(statistics.fmean(x.tokens for x in o)),
        "usd_per_q_list_price": round(statistics.fmean(x.usd for x in o), 5),
        "degraded": sum(x.status == "degraded" for x in o),
        "reflected": sum(x.reflections > 0 for x in o),
        "memory_items": r["memory"],
        "gate": r["gate"],
    }
    if base is not None:
        m, lo, hi = paired_ci([x.f1 for x in base], [x.f1 for x in o])
        row["f1_delta_vs_off"] = [round(m, 3), round(lo, 3), round(hi, 3)]
    return row


def context_profile(o: list[Outcome]) -> list[int]:
    by_step: dict[int, list[int]] = {}
    for x in o:
        for i, t in enumerate(x.context_tokens):
            by_step.setdefault(i, []).append(t)
    return [round(statistics.fmean(v)) for _, v in sorted(by_step.items())]


def to_markdown(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    lines = [
        f"HotpotQA distractor validation, first {meta['questions']} questions, {meta['docs']} "
        f"paragraphs indexed. Model {MODEL}. Spend this run ${meta['spend_usd']:.2f} "
        f"({meta['calls']} new calls, {meta['cached']} cached).",
        "",
        "| Arm | EM | F1 | F1 (2nd half) | ΔF1 vs off [95% CI] | Steps | Tokens/q | Memory | Gate |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.get("not_measured"):
            lines.append(f"| {r['arm']} | NOT MEASURED ({r['not_measured']}) |||||||| ")
            continue
        d = r.get("f1_delta_vs_off")
        ds = f"{d[0]:+.3f} [{d[1]:+.3f}, {d[2]:+.3f}]" if d else "-"
        gate = ", ".join(f"{k} {v}" for k, v in sorted(r["gate"].items())) or "-"
        lines.append(
            f"| {r['arm']} | {r['em']:.3f} | {r['f1']:.3f} | {r['f1_second_half']:.3f} | {ds} | "
            f"{r['steps_mean']} | {r['tokens_per_q']} | {r['memory_items']} | {gate} |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--questions", type=int, default=100)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--cache", type=Path, default=Path(".cache"))
    ap.add_argument("--out", type=Path, default=Path("results/agent"))
    ap.add_argument("--gate-tolerance", type=float, default=0.05)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    arms = ["off", "on", "gated"]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        skipped = [{"arm": n, "not_measured": "no ANTHROPIC_API_KEY"} for n in arms]
        (a.out / "ablation.md").write_text(
            to_markdown(
                skipped,
                {"questions": a.questions, "docs": 0, "spend_usd": 0, "calls": 0, "cached": 0},
            )
        )
        print((a.out / "ablation.md").read_text())
        return

    from retrieval.embedding import FastEmbedEmbedder

    qs = load(a.questions, a.cache / "hotpot")
    docs = corpus(qs)
    emb: Embedder = LockedEmbedder(FastEmbedEmbedder())
    store = PgStore(a.dsn, emb.dim)
    store.migrate()
    store.delete_tenant(TENANT)
    t0 = time.perf_counter()
    index_documents(store, emb, [Document(TENANT, t, text) for t, text in docs.items()])
    print(f"indexed {len(docs)} paragraphs in {time.perf_counter() - t0:.1f}s", flush=True)

    spend = Spend(a.max_usd)
    model = CachedModel(
        AnthropicModel(MODEL, max_tokens=1024, extra=HAIKU_EXTRA),
        a.cache / "agent_llm" / f"{MODEL}.jsonl",
        spend,
    )
    run = make_runner(model, store, emb)
    distill = llm_distiller(model)

    results: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for n in arms:
        try:
            results[n] = arm(n, qs, run, distill, emb, a.batch, a.workers)
            base = results["off"]["outcomes"] if n != "off" and "off" in results else None
            rows.append(summarize(n, results[n], base))
        except BudgetExceeded as e:
            rows.append({"arm": n, "not_measured": str(e)})
    meta = {
        "questions": len(qs),
        "docs": len(docs),
        "spend_usd": spend.usd,
        "calls": spend.calls,
        "cached": spend.cached,
    }
    md = to_markdown(rows, meta)
    if "off" in results:
        md += "\n\nMean estimated context tokens by model call (arm off, compaction on): "
        md += ", ".join(map(str, context_profile(results["off"]["outcomes"])[:10]))
    (a.out / "ablation.md").write_text(md + "\n")
    (a.out / "ablation.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "rows": rows,
                "outcomes": {n: [o.__dict__ for o in r["outcomes"]] for n, r in results.items()},
            },
            indent=1,
        )
    )
    print(md)

    # eval-gate:agent: gated memory must not make answers worse than no memory.
    by = {r["arm"]: r for r in rows if not r.get("not_measured")}
    if "off" in by and "gated" in by and by["gated"]["f1"] < by["off"]["f1"] - a.gate_tolerance:
        print(
            f"EVAL GATE FAILED: gated F1 {by['gated']['f1']} < off {by['off']['f1']} "
            f"- {a.gate_tolerance}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
