"""Phase 5 guardrails (ADR 009). Tool I/O is governed by mcp-rbac-gateway (ADR 006); this
package covers what the gateway never sees: the user turn, the final answer, and how untrusted
tool output is framed for the model."""

from guardrails.pii import PiiGuard, Redaction
from guardrails.spotlight import RULE, Guardrails, spotlight

__all__ = ["RULE", "Guardrails", "PiiGuard", "Redaction", "spotlight"]
