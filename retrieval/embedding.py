"""Embedder interface plus a deterministic fake (tests) and a local ONNX model (eval/CI)."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np
import numpy.typing as npt

Vec = npt.NDArray[np.float32]
_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[Vec]: ...
    def embed_query(self, text: str) -> Vec: ...


class HashEmbedder:
    """Feature-hashed bag of words, L2-normalised. Deterministic and offline: unit tests only."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.name = f"hash-bow-{dim}"

    def _one(self, text: str) -> Vec:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in _TOKEN.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def embed_documents(self, texts: list[str]) -> list[Vec]:
        return [self._one(t) for t in texts]

    def embed_query(self, text: str) -> Vec:
        return self._one(text)


class FastEmbedEmbedder:
    """Local CPU embedding via fastembed (ONNX). Model weights download on first use."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", batch_size: int = 64) -> None:
        from fastembed import TextEmbedding

        self._m = TextEmbedding(model_name=model)
        self.name = model
        self.batch_size = batch_size
        self.dim = len(next(iter(self._m.embed(["probe"]))))

    def embed_documents(self, texts: list[str]) -> list[Vec]:
        return [
            np.asarray(v, dtype=np.float32)
            for v in self._m.embed(texts, batch_size=self.batch_size)
        ]

    def embed_query(self, text: str) -> Vec:
        # bge models use an instruction prefix for queries; fastembed applies it in query_embed.
        return np.asarray(next(iter(self._m.query_embed(text))), dtype=np.float32)
