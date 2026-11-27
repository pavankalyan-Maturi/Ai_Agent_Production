"""Chat model interface. CI uses ScriptedModel; real Claude sits behind the same Protocol.

LiteLLM proxy routing arrives in Phase 4; until then AnthropicModel calls the SDK directly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]  # neutral: {"role", "content", "tool_calls"?, "tool_call_id"?, "raw"?}

# USD per million tokens (input, output). Sonnet 5.5 / Haiku 5.5 list prices; mock is free.
PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "scripted": (0.0, 0.0),
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Decision:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None  # provider content blocks, replayed verbatim next turn

    @property
    def final(self) -> bool:
        return not self.tool_calls


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * pin + output_tokens * pout) / 1e6


class ChatModel(Protocol):
    name: str

    def decide(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Decision: ...


@dataclass
class ScriptedModel:
    """Deterministic model for CI: a policy maps the transcript to the next decision."""

    policy: Callable[[Sequence[Message]], Decision]
    name: str = "scripted"
    calls: int = field(default=0)

    def decide(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Decision:
        self.calls += 1
        return self.policy(messages)


class AnthropicModel:
    """Claude via the Anthropic SDK with client-side tools. UNVERIFIED: not called in CI."""

    def __init__(
        self,
        model: str = "claude-sonnet-5-5",
        max_tokens: int = 4096,
        extra: dict[str, Any] | None = None,
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(max_retries=5)
        self.name = model
        self.max_tokens = max_tokens
        # e.g. Haiku 5.5 in evals: {"thinking": {"type": "disabled"}, "output_config": {...}}
        self.extra = extra or {}

    def decide(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Decision:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        kwargs: dict[str, Any] = dict(self.extra)
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        resp = self._client.messages.create(
            model=self.name,
            max_tokens=self.max_tokens,
            messages=_to_anthropic(messages),  # type: ignore[arg-type]  # plain dict params
            **kwargs,
        )
        calls = tuple(
            ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"
        )
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        if resp.stop_reason == "refusal":
            calls, text = (), text or "[refused]"
        return Decision(
            text=text,
            tool_calls=calls,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            raw=[b.model_dump(exclude_none=True) for b in resp.content],
        )


def _blocks(m: Message) -> list[dict[str, Any]]:
    """Rebuild assistant blocks when raw ones were dropped (compacted turns, other providers)."""
    out: list[dict[str, Any]] = []
    if m.get("content"):
        out.append({"type": "text", "text": m["content"]})
    for c in m.get("tool_calls") or ():
        out.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]})
    return out or [{"type": "text", "text": "."}]


def _to_anthropic(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "assistant":
            # Replay provider blocks (incl. thinking) unchanged; history stays append-only.
            content = m.get("raw") or _blocks(m)
            out.append({"role": "assistant", "content": content})
        elif m["role"] == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            out.append({"role": "user", "content": m["content"]})
    return out
