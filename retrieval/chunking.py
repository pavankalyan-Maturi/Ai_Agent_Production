"""Word-window chunker. Deterministic ids make re-ingestion idempotent."""

from __future__ import annotations

import hashlib

from retrieval.types import Chunk, Document


def chunk_id(tenant_id: str, doc_id: str, ord_: int, text: str) -> str:
    h = hashlib.sha256(f"{tenant_id}\x00{doc_id}\x00{ord_}\x00{text}".encode()).hexdigest()
    return h[:32]


def chunk_document(doc: Document, size: int = 96, overlap: int = 16) -> list[Chunk]:
    """Split into windows of `size` words with `overlap` words carried over.

    Words, not tokens: tokenizer-independent and good enough for English corpora.
    The title, if any, is prepended to the first window only (as the source text has it).
    """
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    body = f"{doc.title}. {doc.text}" if doc.title else doc.text
    words = body.split()
    if not words:
        return []
    step = size - overlap
    chunks: list[Chunk] = []
    for i, start in enumerate(range(0, len(words), step)):
        window = words[start : start + size]
        text = " ".join(window)
        chunks.append(
            Chunk(doc.tenant_id, doc.doc_id, chunk_id(doc.tenant_id, doc.doc_id, i, text), i, text)
        )
        if start + size >= len(words):
            break
    return chunks
