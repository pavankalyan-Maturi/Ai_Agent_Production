"""Context compaction: cap each tool result, and summarise old observations past a threshold.

The transcript in state stays append-only (audit, replay). Compaction only shapes the *view*
sent to the model: tool results before `compacted_upto` are replaced by a stub and one summary
goes into the system message. tool_use / tool_result pairs survive, so the view stays valid.
Compacted assistant turns lose their raw provider blocks (incl. thinking) and are rebuilt from
`tool_calls`; UNVERIFIED against preserved-thinking checks on live models.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

Message = dict[str, Any]
STUB = "[compacted: see summary of earlier findings]"


@dataclass(frozen=True)
class CompactionPolicy:
    tool_result_chars: int = 2000  # hard cap per observation, applied when it is recorded
    threshold_tokens: int = 6000  # summarise when the view is estimated above this
    keep_last: int = 4  # newest messages never compacted


def estimate_tokens(messages: Sequence[Message]) -> int:
    """~4 chars per token. An ESTIMATE, used only to decide when to compact."""
    n = 0
    for m in messages:
        n += len(m.get("content") or "") + 16
        for c in m.get("tool_calls") or ():
            n += len(c["name"]) + len(str(c["args"]))
    return n // 4


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 3 // 4
    tail = max_chars - head
    return f"{text[:head]}\n[... {len(text) - max_chars} chars truncated ...]\n{text[-tail:]}"


def view(messages: Sequence[Message], compacted_upto: int, summary: str) -> list[Message]:
    out: list[Message] = []
    for i, m in enumerate(messages):
        if i == 0 and m["role"] == "system" and summary:
            m = {**m, "content": f"{m['content']}\n\nSummary of earlier findings:\n{summary}"}
        elif i < compacted_upto and m["role"] == "tool":
            m = {**m, "content": STUB}
        elif i < compacted_upto and m["role"] == "assistant":
            m = {k: v for k, v in m.items() if k != "raw"}
        out.append(m)
    return out


def cut_point(messages: Sequence[Message], keep_last: int) -> int:
    """Index before which messages may be compacted; never splits an assistant/tool group."""
    cut = max(2, len(messages) - keep_last)
    while cut < len(messages) and messages[cut]["role"] == "tool":
        cut += 1
    return cut


Summarizer = Callable[[str, Sequence[str]], tuple[str, int, int]]  # -> (summary, in, out tokens)


def extractive_summarizer(max_chars: int = 1200) -> Summarizer:
    """No-model summariser: first line of each observation. Free; used in CI and as fallback."""

    def fn(previous: str, observations: Sequence[str]) -> tuple[str, int, int]:
        lines = [previous] if previous else []
        lines += [f"- {o.strip().splitlines()[0][:200]}" for o in observations if o.strip()]
        return truncate("\n".join(lines), max_chars), 0, 0

    return fn
