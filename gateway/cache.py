"""Tenant-scoped exact + semantic response cache in Redis (ADR 011).

Exact: key = sha256 over canonical JSON of (model, messages, params), namespaced by tenant.
Semantic: only for single-turn prompts (one user message, no tool traffic) at temperature 0.
Entries live per (tenant, model, system prompt, params) namespace, capped at `max_entries`
(LPUSH + LTRIM), scanned brute-force with numpy: at <=256 vectors of 384 dims this is ~0.1 ms
and needs no Redis vector module. A hit needs cosine >= threshold AND, with `guard` on, the
same salient tokens (numbers, negations, quoted strings): embeddings blur "2023" vs "2024"
and "is" vs "is not", which is where semantic caches return confidently wrong answers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from retrieval.embedding import Embedder

_NUM = re.compile(r"(?<![a-z])\d+(?:[.,]\d+)*")
_QUOTED = re.compile(r"\"([^\"]+)\"|'([^']+)'")
_NEG = frozenset({"not", "no", "never", "without", "except", "isn't", "doesn't", "don't", "can't"})


@dataclass(frozen=True)
class Hit:
    kind: str  # "exact" | "semantic"
    text: str
    input_tokens: int
    output_tokens: int
    score: float = 1.0


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()


def salient(text: str) -> tuple[str, ...]:
    t = text.lower()
    toks = set(_NUM.findall(t)) | {a or b for a, b in _QUOTED.findall(t)}
    toks |= {w for w in re.findall(r"[a-z']+", t) if w in _NEG}
    return tuple(sorted(toks))


def semantic_query(messages: Sequence[dict[str, Any]], params: dict[str, Any]) -> str | None:
    """The user prompt if the request is semantically cacheable, else None."""
    if params.get("temperature") not in (None, 0, 0.0) or params.get("tools"):
        return None
    users = [m for m in messages if m.get("role") == "user"]
    others = [m for m in messages if m.get("role") not in ("user", "system")]
    if len(users) != 1 or others or not isinstance(users[0].get("content"), str):
        return None
    return str(users[0]["content"])


class LLMCache:
    def __init__(
        self,
        redis: Any,
        embedder: Embedder | None = None,
        *,
        ttl_s: int = 3600,
        threshold: float = 0.9,
        max_entries: int = 256,
        guard: bool = True,
        prefix: str = "llm",
    ) -> None:
        self._r = redis
        self.embedder = embedder  # None = exact cache only
        self.ttl_s, self.threshold, self.max_entries = ttl_s, threshold, max_entries
        self.guard, self.prefix = guard, prefix

    def exact_key(
        self, tenant: str, model: str, messages: Sequence[Any], params: dict[str, Any]
    ) -> str:
        return f"{self.prefix}:exact:{tenant}:{_sha([model, list(messages), params])}"

    def semantic_ns(
        self, tenant: str, model: str, messages: Sequence[Any], params: dict[str, Any]
    ) -> str:
        system = [m.get("content") for m in messages if m.get("role") == "system"]
        return f"{self.prefix}:sem:{tenant}:{_sha([model, system, params])}"

    async def get(
        self, tenant: str, model: str, messages: Sequence[dict[str, Any]], params: dict[str, Any]
    ) -> Hit | None:
        raw = await self._r.get(self.exact_key(tenant, model, messages, params))
        if raw:
            d = json.loads(raw)
            return Hit("exact", d["text"], d["in"], d["out"])
        q = semantic_query(messages, params)
        if q is None or self.embedder is None:
            return None
        entries = await self._r.lrange(self.semantic_ns(tenant, model, messages, params), 0, -1)
        if not entries:
            return None
        rows = [json.loads(e) for e in entries]
        mat = np.stack([np.frombuffer(base64.b64decode(r["v"]), dtype=np.float32) for r in rows])
        scores = mat @ self.embedder.embed_query(q)
        sal = list(salient(q))
        for i in np.argsort(-scores):
            if scores[i] < self.threshold:
                break
            if not self.guard or rows[i]["s"] == sal:
                r = rows[i]
                return Hit("semantic", r["text"], r["in"], r["out"], float(scores[i]))
        return None

    async def put(
        self,
        tenant: str,
        model: str,
        messages: Sequence[dict[str, Any]],
        params: dict[str, Any],
        text: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        payload = {"text": text, "in": input_tokens, "out": output_tokens}
        key = self.exact_key(tenant, model, messages, params)
        await self._r.set(key, _canon(payload), ex=self.ttl_s)
        q = semantic_query(messages, params)
        if q is None or self.embedder is None:
            return
        vec = np.asarray(self.embedder.embed_query(q), dtype=np.float32)
        entry = payload | {"v": base64.b64encode(vec.tobytes()).decode(), "s": list(salient(q))}
        ns = self.semantic_ns(tenant, model, messages, params)
        pipe = self._r.pipeline()
        pipe.lpush(ns, _canon(entry))
        pipe.ltrim(ns, 0, self.max_entries - 1)
        pipe.expire(ns, self.ttl_s)
        await pipe.execute()
