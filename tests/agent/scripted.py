"""Scripted policies shared by unit tests, the crash worker and the bench."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.model import Decision, Message, ToolCall


def n_assistant(messages: Sequence[Message]) -> int:
    return sum(1 for m in messages if m["role"] == "assistant")


def last_tool(messages: Sequence[Message]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == "tool"), "")


def plan_policy(steps: list[tuple[str, dict[str, Any]] | str], tokens: tuple[int, int] = (100, 20)):
    """steps[i] is the decision at model call i: (tool, args) or a final answer template.

    A final template may contain {last} for the most recent tool observation.
    """

    def policy(messages: Sequence[Message]) -> Decision:
        i = n_assistant(messages)
        step = steps[min(i, len(steps) - 1)]
        if isinstance(step, str):
            return Decision(
                text=step.format(last=last_tool(messages)),
                input_tokens=tokens[0],
                output_tokens=tokens[1],
            )
        name, args = step
        return Decision(
            tool_calls=(ToolCall(f"call_{i}", name, args),),
            input_tokens=tokens[0],
            output_tokens=tokens[1],
        )

    return policy


def loop_policy(tokens: tuple[int, int] = (1000, 200)):
    """Never finishes: always asks for another calculation."""

    def policy(messages: Sequence[Message]) -> Decision:
        i = n_assistant(messages)
        return Decision(
            tool_calls=(ToolCall(f"c{i}", "calculator", {"expression": f"{i}+1"}),),
            input_tokens=tokens[0],
            output_tokens=tokens[1],
        )

    return policy
