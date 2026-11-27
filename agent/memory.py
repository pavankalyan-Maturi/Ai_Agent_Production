"""Experience memory (P2.5), ReasoningBank-style (Ouyang et al. 2025).

After a run, a distiller turns the trajectory plus an *external* outcome (grader, user feedback,
tests) into short, reusable strategy items: what worked on a success, what to avoid on a failure.
Items are recalled by embedding similarity at the start of later runs. Everything is per tenant.

Unfiltered memory can hurt: wrong lessons, near-duplicates and leaked answers pile up. The
admission gate admits an item only if it passes static checks *and* a replay of the source task
with the item does at least as well as the run without it.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from agent.model import ChatModel, Decision
from retrieval.embedding import Embedder

Outcome = Literal["success", "failure"]


@dataclass(frozen=True)
class MemoryItem:
    title: str
    content: str
    outcome: Outcome
    source: str = ""  # source question, kept for audit; never shown to the model

    @property
    def id(self) -> str:
        return hashlib.sha256(f"{self.title}\x00{self.content}".encode()).hexdigest()[:16]

    def render(self) -> str:
        tag = "Strategy" if self.outcome == "success" else "Pitfall"
        return f"- {tag}: {self.title}. {self.content}"


class MemoryBank(Protocol):
    def add(self, tenant_id: str, item: MemoryItem, vec: np.ndarray) -> None: ...

    def search(self, tenant_id: str, vec: np.ndarray, k: int) -> list[tuple[MemoryItem, float]]: ...

    def count(self, tenant_id: str) -> int: ...


@dataclass
class InMemoryBank:
    """For tests and the offline eval. Same tenant semantics as PgMemoryBank."""

    _rows: dict[str, list[tuple[MemoryItem, np.ndarray]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, tenant_id: str, item: MemoryItem, vec: np.ndarray) -> None:
        with self._lock:
            rows = self._rows.setdefault(tenant_id, [])
            if all(i.id != item.id for i, _ in rows):
                rows.append((item, vec / (np.linalg.norm(vec) or 1.0)))

    def search(self, tenant_id: str, vec: np.ndarray, k: int) -> list[tuple[MemoryItem, float]]:
        rows = self._rows.get(tenant_id, [])
        if not rows:
            return []
        q = vec / (np.linalg.norm(vec) or 1.0)
        scored = sorted(((i, float(v @ q)) for i, v in rows), key=lambda t: -t[1])
        return scored[:k]

    def count(self, tenant_id: str) -> int:
        return len(self._rows.get(tenant_id, []))


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_memory (
    tenant_id  text NOT NULL,
    id         text NOT NULL,
    title      text NOT NULL,
    content    text NOT NULL,
    outcome    text NOT NULL CHECK (outcome IN ('success', 'failure')),
    source     text NOT NULL DEFAULT '',
    embedding  vector({dim}) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS agent_memory_tenant ON agent_memory (tenant_id);
"""


class PgMemoryBank:
    """Postgres + pgvector. Small per-tenant sets, so an exact scan filtered by tenant."""

    def __init__(self, dsn: str, dim: int) -> None:
        self.dsn, self.dim = dsn, dim

    def _conn(self) -> psycopg.Connection:
        conn = psycopg.connect(self.dsn, prepare_threshold=None)
        register_vector(conn)
        return conn

    def migrate(self) -> None:
        with psycopg.connect(self.dsn, prepare_threshold=None) as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('pap.migrate.agent_memory'))")
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(SCHEMA.format(dim=int(self.dim)))

    def add(self, tenant_id: str, item: MemoryItem, vec: np.ndarray) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO agent_memory (tenant_id, id, title, content, outcome, source, "
                "embedding) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    tenant_id,
                    item.id,
                    item.title,
                    item.content,
                    item.outcome,
                    item.source,
                    np.asarray(vec, dtype=np.float32),
                ),
            )

    def search(self, tenant_id: str, vec: np.ndarray, k: int) -> list[tuple[MemoryItem, float]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT title, content, outcome, source, 1 - (embedding <=> %s) AS sim "
                "FROM agent_memory WHERE tenant_id = %s ORDER BY embedding <=> %s, id LIMIT %s",
                (
                    np.asarray(vec, dtype=np.float32),
                    tenant_id,
                    np.asarray(vec, dtype=np.float32),
                    k,
                ),
            ).fetchall()
        return [(MemoryItem(r[0], r[1], r[2], r[3]), float(r[4])) for r in rows]

    def count(self, tenant_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count(*) FROM agent_memory WHERE tenant_id = %s", (tenant_id,)
            ).fetchone()
        return int(row[0]) if row else 0


@dataclass
class Recall:
    """What build_graph uses at the start of a run."""

    bank: MemoryBank
    embedder: Embedder
    k: int = 3
    min_sim: float = 0.3

    def __call__(self, tenant_id: str, question: str) -> list[MemoryItem]:
        vec = np.asarray(self.embedder.embed_query(question))
        return [i for i, s in self.bank.search(tenant_id, vec, self.k) if s >= self.min_sim]


# ---------- distillation ----------

DISTILL_PROMPT = """You extract reusable lessons from an agent's trajectory.
The run was judged a {outcome} by an external grader.
{guidance}
Return JSON only: [{{"title": "...", "content": "..."}}] with 1-2 items.
Each item is a general strategy for *similar future questions*. It must not contain this
question's answer, names or numbers specific to it. Max 300 characters of content.

Question: {question}

Trajectory:
{trajectory}"""

SUCCESS = "Say which search/fetch moves led to the answer, as a strategy."
FAILURE = "Say what went wrong and what to do instead, as a pitfall to avoid."


def render_trajectory(messages: Sequence[dict[str, Any]], max_chars: int = 4000) -> str:
    lines = []
    for m in messages:
        if m["role"] == "assistant":
            for c in m.get("tool_calls") or ():
                lines.append(f"ACTION {c['name']}({json.dumps(c['args'])[:200]})")
            if m.get("content") and not m.get("tool_calls"):
                lines.append(f"ANSWER {m['content'][:300]}")
        elif m["role"] == "tool":
            lines.append(f"OBSERVATION {(m['content'] or '')[:200]}")
        elif m["role"] == "user" and lines:
            lines.append(f"FEEDBACK {m['content'][:200]}")
    return "\n".join(lines)[-max_chars:]


Distiller = Callable[[str, Sequence[dict[str, Any]], Outcome], tuple[list[MemoryItem], int, int]]


def llm_distiller(model: ChatModel) -> Distiller:
    def fn(
        question: str, messages: Sequence[dict[str, Any]], outcome: Outcome
    ) -> tuple[list[MemoryItem], int, int]:
        prompt = DISTILL_PROMPT.format(
            outcome=outcome,
            guidance=SUCCESS if outcome == "success" else FAILURE,
            question=question,
            trajectory=render_trajectory(messages),
        )
        d: Decision = model.decide([{"role": "user", "content": prompt}], [])
        return parse_items(d.text, outcome, question), d.input_tokens, d.output_tokens

    return fn


def parse_items(text: str, outcome: Outcome, source: str) -> list[MemoryItem]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = []
    for r in raw[:2] if isinstance(raw, list) else []:
        if isinstance(r, dict) and r.get("title") and r.get("content"):
            items.append(MemoryItem(str(r["title"])[:80], str(r["content"])[:400], outcome, source))
    return items


# ---------- admission gate ----------


@dataclass(frozen=True)
class RunResult:
    score: float  # external grade in [0, 1]
    steps: int


Replay = Callable[[MemoryItem], RunResult]  # re-run the source task with only this item recalled


@dataclass
class AdmissionGate:
    bank: MemoryBank
    embedder: Embedder
    dup_sim: float = 0.92
    min_chars: int = 20
    max_chars: int = 400
    stats: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _reject(self, why: str) -> bool:
        with self._lock:
            self.stats[why] = self.stats.get(why, 0) + 1
        return False

    def _is_dup(self, tenant_id: str, vec: np.ndarray) -> bool:
        hits = self.bank.search(tenant_id, vec, 1)
        return bool(hits) and hits[0][1] >= self.dup_sim

    def static_ok(self, tenant_id: str, item: MemoryItem, answer: str) -> bool:
        """Cheap checks first, so replays are only paid for plausible items."""
        n = len(item.content)
        if n < self.min_chars or n > self.max_chars:
            return self._reject("length")
        text = f"{item.title} {item.content}".lower()
        if answer and len(answer) >= 3 and answer.lower() in text:
            return self._reject("answer_leak")
        if re.search(r"(api[_-]?key|password|secret|token)\s*[:=]", text):
            return self._reject("secret")
        if self._is_dup(tenant_id, self._vec(item)):
            return self._reject("duplicate")
        return True

    def _vec(self, item: MemoryItem) -> np.ndarray:
        return np.asarray(self.embedder.embed_query(f"{item.title}. {item.content}"))

    def admit(
        self, tenant_id: str, item: MemoryItem, answer: str, baseline: RunResult, replay: Replay
    ) -> bool:
        if not self.static_ok(tenant_id, item, answer):
            return False
        with_item = replay(item)
        # Admit if the item fixes something, or keeps an already-solved task solved without extra
        # steps. A neutral item on a failed task is noise and is rejected.
        better = with_item.score > baseline.score
        kept = (
            with_item.score >= 1.0 and baseline.score >= 1.0 and with_item.steps <= baseline.steps
        )
        if not (better or kept):
            return self._reject("replay_worse")
        vec = self._vec(item)
        with self._lock:  # replays run in parallel: re-check duplicates atomically with the add
            if self._is_dup(tenant_id, vec):
                self.stats["duplicate"] = self.stats.get("duplicate", 0) + 1
                return False
            self.bank.add(tenant_id, item, vec)
            self.stats["admitted"] = self.stats.get("admitted", 0) + 1
        return True


def admit_ungated(bank: MemoryBank, embedder: Embedder, tenant_id: str, item: MemoryItem) -> None:
    bank.add(tenant_id, item, np.asarray(embedder.embed_query(f"{item.title}. {item.content}")))
