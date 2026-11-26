"""Graph state and budgets. State holds only JSON-friendly values so checkpoints stay portable."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

Status = Literal["running", "done", "degraded"]


class AgentState(TypedDict, total=False):
    tenant_id: str  # set by the caller from the authenticated request, never by the model
    question: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    steps: int  # model calls made
    tokens: int  # input + output tokens
    usd: float
    status: Status
    stop_reason: str  # "final" | "max_steps" | "max_tokens" | "max_usd"
    answer: str


@dataclass(frozen=True)
class Budget:
    max_steps: int = 8
    max_tokens: int = 50_000
    max_usd: float = 0.25

    def exhausted(self, s: AgentState) -> str | None:
        if s.get("steps", 0) >= self.max_steps:
            return "max_steps"
        if s.get("tokens", 0) >= self.max_tokens:
            return "max_tokens"
        if s.get("usd", 0.0) >= self.max_usd:
            return "max_usd"
        return None
