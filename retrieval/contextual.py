"""Contextual retrieval (Anthropic, Sep 2024): prepend an LLM-written situating sentence to each
chunk before embedding and FTS indexing.

Cost controls: Haiku 5.5 at low effort, the document is the cached prefix shared by all of its
chunks (min cacheable prefix on Haiku 5.5 is 512 tokens, so short documents won't cache), and a
content-addressed on-disk cache so a corpus is only paid for once per prompt version.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from retrieval.types import Chunk

PROMPT_VERSION = "v1"
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
