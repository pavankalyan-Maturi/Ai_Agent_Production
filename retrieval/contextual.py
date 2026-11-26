"""Contextual retrieval (Anthropic, Sep 2024): prepend an LLM-written situating sentence to each
chunk before embedding and FTS indexing.

Two providers behind one interface, same prompt:
- GroqContextualizer (default in CI): free tier, open-weight Llama. Rate-limited, so it honours
  429 retry-after and the eval runs it against a time budget, resuming from the cache next run.
- AnthropicContextualizer: Claude Haiku 5.5 at low effort, document as cached prefix (min cacheable
  prefix 512 tokens, so short documents won't cache).
Both write to a content-addressed disk cache keyed by (prompt version, model, doc, chunk), so a
corpus is generated once per prompt version and model.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from retrieval.types import Chunk

PROMPT_VERSION = "v2"  # v2: gpt-oss reasoning_effort=low; v1 Groq contexts discarded
DEFAULT_MODEL = "claude-haiku-5-5"

DOC_PROMPT = "<document>\n{doc}\n</document>"
CHUNK_PROMPT = (
    "Here is the chunk we want to situate within the whole document\n"
    "<chunk>\n{chunk}\n</chunk>\n"
    "Please give a short succinct context to situate this chunk within the overall document "
    "for the purposes of improving search retrieval of the chunk. "
    "Answer only with the succinct context and nothing else."
)


class Contextualizer(Protocol):
    name: str

    def contextualize(self, doc_text: str, chunks: list[Chunk]) -> list[Chunk]: ...


@dataclass
class Usage:
    calls: int = 0
    cache_hits_local: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class MockContextualizer:
    """Deterministic stand-in: first sentence of the document. Unit tests and keyless CI."""

    name = "mock-first-sentence"

    def contextualize(self, doc_text: str, chunks: list[Chunk]) -> list[Chunk]:
        first = doc_text.split(". ")[0][:200]
        return [replace(c, context=f"From a document about: {first}") for c in chunks]


class ContextBudgetExceeded(Exception):
    """The time budget ran out; what was generated so far is in the cache."""


class DiskCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._d: dict[str, str] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                self._d[rec["k"]] = rec["v"]

    def get(self, key: str) -> str | None:
        return self._d.get(key)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._d[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps({"k": key, "v": value}) + "\n")


def cache_key(model: str, doc_text: str, chunk_text: str) -> str:
    raw = f"{PROMPT_VERSION}\x00{model}\x00{doc_text}\x00{chunk_text}"
    return hashlib.sha256(raw.encode()).hexdigest()


class AnthropicContextualizer:
    def __init__(
        self, cache: DiskCache, model: str = DEFAULT_MODEL, max_tokens: int = 1024
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(max_retries=5)
        self.model = model
        self.name = f"anthropic:{model}:{PROMPT_VERSION}"
        self.cache = cache
        self.max_tokens = max_tokens
        self.usage = Usage()
        self._lock = threading.Lock()

    def _call(self, doc_text: str, chunk_text: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            output_config={"effort": "low"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": DOC_PROMPT.format(doc=doc_text),
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": CHUNK_PROMPT.format(chunk=chunk_text)},
                    ],
                }
            ],
        )
        if resp.stop_reason == "refusal":
            return ""
        u = resp.usage
        with self._lock:
            self.usage.calls += 1
            self.usage.input_tokens += u.input_tokens
            self.usage.output_tokens += u.output_tokens
            self.usage.cache_read_input_tokens += u.cache_read_input_tokens or 0
            self.usage.cache_creation_input_tokens += u.cache_creation_input_tokens or 0
        return " ".join(b.text for b in resp.content if b.type == "text").strip()

    def contextualize(self, doc_text: str, chunks: list[Chunk]) -> list[Chunk]:
        out = []
        # Sequential within a document so chunks 2..n can read the doc prefix from cache.
        for c in chunks:
            key = cache_key(self.model, doc_text, c.content)
            ctx = self.cache.get(key)
            if ctx is None:
                ctx = self._call(doc_text, c.content)
                self.cache.put(key, ctx)
            else:
                with self._lock:
                    self.usage.cache_hits_local += 1
            out.append(replace(c, context=ctx))
        return out


def _redact(text: str) -> str:
    """Drop account identifiers from provider error bodies before they reach CI logs."""
    return re.sub(r"org_[A-Za-z0-9]+", "org_***", text)


class GroqContextualizer:
    """Groq's OpenAI-compatible chat completions endpoint, called over HTTP (no extra SDK)."""

    URL = "https://api.groq.com/openai/v1/chat/completions"
    MODELS_URL = "https://api.groq.com/openai/v1/models"
    # Groq retires models often; non-reasoning models first so max_tokens goes to the answer.
    FALLBACKS = (
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "openai/gpt-oss-20b",
    )

    @classmethod
    def resolve_model(cls, api_key: str, preferred: str, transport: object = None) -> str:
        """Return `preferred` if the account can use it, else the first available fallback."""
        import httpx2

        kw = {"transport": transport} if transport is not None else {}
        with httpx2.Client(timeout=30, **kw) as http:  # type: ignore[arg-type]
            r = http.get(cls.MODELS_URL, headers={"Authorization": f"Bearer {api_key}"})
        if r.is_error:
            raise RuntimeError(f"Groq models {r.status_code}: {r.text[:300]}")
        ids = {m["id"] for m in r.json().get("data", []) if m.get("active", True)}
        for m in (preferred, *cls.FALLBACKS):
            if m in ids:
                return m
        raise RuntimeError(f"Groq: none of {[preferred, *cls.FALLBACKS]} in {sorted(ids)}")

    def __init__(
        self,
        cache: DiskCache,
        api_key: str,
        model: str = "llama-3.1-8b-instant",
        deadline: float | None = None,
        max_tokens: int = 200,
    ) -> None:
        import httpx2

        self._http = httpx2.Client(timeout=60, headers={"Authorization": f"Bearer {api_key}"})
        self.model = model
        self.name = f"groq:{model}:{PROMPT_VERSION}"
        self.cache = cache
        self.deadline = deadline  # time.monotonic() value; None = no budget
        self.max_tokens = max_tokens
        self.usage = Usage()
        self._lock = threading.Lock()

    def _call(self, doc_text: str, chunk_text: str) -> str:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": DOC_PROMPT.format(doc=doc_text)
                    + "\n\n"
                    + CHUNK_PROMPT.format(chunk=chunk_text),
                }
            ],
        }
        if self.model.startswith("openai/gpt-oss"):
            # Reasoning model: keep reasoning short and out of the returned content.
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
            body["max_tokens"] = self.max_tokens + 300
        last = ""
        errors = 0  # 5xx only; per-minute 429s just wait, bounded by the deadline
        while errors < 8:
            if self.deadline is not None and time.monotonic() > self.deadline:
                raise ContextBudgetExceeded(f"deadline; last: {last}")
            r = self._http.post(self.URL, json=body)
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("retry-after", 2**errors))
                last = f"{r.status_code} retry-after={wait:.0f}s {_redact(r.text[:200])}"
                if r.status_code == 429 and wait > 120:
                    # Daily quota, not a per-minute limit: stop; the disk cache resumes next run.
                    raise ContextBudgetExceeded(f"Groq quota: {last}")
                if r.status_code >= 500:
                    errors += 1
                time.sleep(min(wait, 60))
                continue
            if r.is_error:
                raise RuntimeError(f"Groq {r.status_code}: {_redact(r.text[:300])}")
            data = r.json()
            u = data.get("usage", {})
            with self._lock:
                self.usage.calls += 1
                self.usage.input_tokens += int(u.get("prompt_tokens", 0))
                self.usage.output_tokens += int(u.get("completion_tokens", 0))
            return str(data["choices"][0]["message"]["content"]).strip()
        raise RuntimeError(f"Groq: 5xx retries exhausted: {last}")

    def contextualize(self, doc_text: str, chunks: list[Chunk]) -> list[Chunk]:
        out = []
        for c in chunks:
            key = cache_key(self.model, doc_text, c.content)
            ctx = self.cache.get(key)
            if ctx is None:
                ctx = self._call(doc_text, c.content)
                self.cache.put(key, ctx)
            else:
                with self._lock:
                    self.usage.cache_hits_local += 1
            out.append(replace(c, context=ctx))
        return out
