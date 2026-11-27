"""P3.3 tool retrieval (RAG-MCP style), behind the TOOL_RETRIEVAL_RAGMCP flag (default off).

With many tools, sending every spec to the model costs tokens and dilutes the choice. The
selector embeds each tool's name + description once and offers the model only the top-k
tools for the question, plus any tool already used in the run (so a multi-step plan never
loses a tool mid-way) and an always-include list. Selection runs after the governance
filter, so it can only narrow what a role may see, never widen it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from agent.model import ToolSpec
from retrieval.embedding import Embedder


def spec_text(spec: ToolSpec) -> str:
    props = " ".join((spec.parameters or {}).get("properties", {}).keys())
    return f"{spec.name.replace('_', ' ')}: {spec.description} {props}".strip()


@dataclass
class ToolSelector:
    embedder: Embedder
    k: int = 5
    always: frozenset[str] = frozenset()
    _index: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def index(self, specs: Iterable[ToolSpec]) -> None:
        new = [s for s in specs if s.name not in self._index]
        if new:
            vecs = self.embedder.embed_documents([spec_text(s) for s in new])
            self._index.update({s.name: np.asarray(v) for s, v in zip(new, vecs, strict=True)})

    def select(
        self, question: str, specs: Sequence[ToolSpec], used: Iterable[str] = ()
    ) -> list[ToolSpec]:
        if len(specs) <= self.k:
            return list(specs)
        self.index(specs)
        q = np.asarray(self.embedder.embed_query(question))
        ranked = sorted(specs, key=lambda s: -float(q @ self._index[s.name]))
        keep = {s.name for s in ranked[: self.k]} | set(used) | set(self.always)
        return [s for s in specs if s.name in keep]  # stable order: keeps prompts cacheable
