"""P5.1 PII and secret redaction on the two edges the tool gateway never sees.

Inbound: the user's question, before the model, memory or a checkpoint sees it. Outbound: the
final answer, before it leaves the graph. Tool arguments and results are already redacted by
mcp-rbac-gateway, so both edges use the library's Scanner: one rule set, one place to tune it.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp_rbac_gateway import Scanner


@dataclass(frozen=True)
class Redaction:
    text: str
    detectors: tuple[str, ...]  # names only, never the matched values


class PiiGuard:
    def __init__(self, scanner: Scanner | None = None) -> None:
        # direction="output" applies every built-in detector (PII + secrets).
        self._scanner = scanner or Scanner()

    def redact(self, text: str) -> Redaction:
        out, findings = self._scanner.redact_text(text, direction="output")
        return Redaction(out, tuple(sorted({f.detector for f in findings})))
