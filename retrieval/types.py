"""Core value types for the retrieval layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Document:
    tenant_id: str
    doc_id: str
    text: str
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chunk:
    tenant_id: str
    doc_id: str
    chunk_id: str
    ord: int
    content: str
    # LLM-generated situating context (contextual retrieval); empty when the flag is off.
    context: str = ""

    @property
    def indexed_text(self) -> str:
        """What gets embedded and full-text indexed."""
        return f"{self.context}\n\n{self.content}" if self.context else self.content


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: str
    doc_id: str
    score: float
    content: str
