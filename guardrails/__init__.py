"""Phase 5 guardrails (ADR 009). Tool I/O is governed by mcp-rbac-gateway (ADR 006); this
package covers what the gateway never sees: the user turn, the final answer, and how untrusted
tool output is framed for the model."""

from guardrails.pii import PiiGuard, Redaction
from guardrails.policy import Policy, Sink, Violation
from guardrails.spotlight import RULE, Guardrails, spotlight
from guardrails.taint import Ledger

__all__ = [
    "RULE",
    "Guardrails",
    "Ledger",
    "PiiGuard",
    "Policy",
    "Redaction",
    "Sink",
    "Violation",
    "spotlight",
]
