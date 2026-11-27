"""Grounded reflection (P2.6): retry only when an external check fails.

A verifier looks at evidence outside the model's own judgement (tool outputs, retrieved ids,
a test, a grader) and returns a failure reason or None. The model is never asked "are you sure?":
self-critique without a signal tends to flip correct answers (Huang et al. 2024).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

Message = dict[str, Any]
Verifier = Callable[[Sequence[Message], str], str | None]

_CITE = re.compile(r"\[([^\[\]\n]{1,200})\]")
_RESULT_ID = re.compile(r"^\[([^\[\]\n]{1,200})\] ", re.M)


def seen_doc_ids(messages: Sequence[Message]) -> set[str]:
    ids: set[str] = set()
    for m in messages:
        if m["role"] == "tool" and m.get("name") in ("search", "fetch_doc"):
            ids |= set(_RESULT_ID.findall(m["content"] or ""))
        if m["role"] == "assistant":
            for c in m.get("tool_calls") or ():
                if c["name"] == "fetch_doc":
                    ids.add(str(c["args"].get("doc_id", "")))
    return ids


def citations_grounded(messages: Sequence[Message], answer: str) -> str | None:
    """Every [doc_id] cited must have come back from search/fetch_doc in this run."""
    cited = set(_CITE.findall(answer))
    if not cited:
        return "the answer cites no [doc_id]; cite the documents the answer comes from"
    unknown = sorted(cited - seen_doc_ids(messages))
    if unknown:
        return f"cited ids never returned by a tool: {', '.join(unknown[:5])}"
    return None


def last_tools_failed(messages: Sequence[Message], answer: str) -> str | None:
    """If the newest tool batch was all errors, the answer is not grounded in anything."""
    batch: list[str] = []
    for m in reversed(messages):
        if m["role"] == "tool":
            batch.append(m["content"] or "")
        elif m["role"] == "assistant" and m.get("tool_calls") and batch:
            break
    if batch and all(b.startswith("error") for b in batch):
        return f"the last tool call failed ({batch[0][:120]}); fix the call or use another tool"
    return None


def reflection_prompt(reason: str) -> str:
    return (
        f"An external check failed: {reason}. Use the tools to fix this, then answer again. "
        "Do not repeat a call that already failed."
    )
