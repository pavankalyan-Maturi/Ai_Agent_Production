"""OpenAI-compatible client for the LiteLLM proxy (sync for the agent, async streaming for SSE).

The agent never talks to a provider directly once PAP_LLM_GATEWAY_URL is set: LiteLLM owns
provider keys, retries and fallbacks, and vLLM (Phase 7) is one more `model_list` entry.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent.model import Decision, Message, ToolCall, ToolSpec


def to_openai(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c["args"])},
                    }
                    for c in m["tool_calls"]
                ]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


@dataclass
class GatewayModel:
    """ChatModel over LiteLLM. Covered by tests against the mock upstream, not a real provider."""

    base_url: str
    api_key: str
    name: str = "agent-default"
    max_tokens: int = 4096
    timeout_s: float = 60.0
    _http: httpx.Client | None = field(default=None, repr=False)

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(base_url=self.base_url, timeout=self.timeout_s)
        return self._http

    def decide(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Decision:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": to_openai(messages),
            "max_tokens": self.max_tokens,
        }
        if tools:
            body["tools"] = _tools(tools)
        r = self._client().post(
            "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {self.api_key}"}
        )
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        calls = tuple(
            ToolCall(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"] or "{}"))
            for c in msg.get("tool_calls") or ()
        )
        usage = data.get("usage") or {}
        return Decision(
            text=(msg.get("content") or "").strip(),
            tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )


@dataclass
class StreamClient:
    """Async streaming chat completions. Leaving the `async for` early (client disconnect,
    cancellation) exits the `stream()` context, which closes the upstream connection; LiteLLM
    then cancels its own provider request."""

    http: httpx.AsyncClient
    api_key: str

    async def stream(
        self, model: str, messages: list[dict[str, Any]], max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with self.http.stream(
            "POST", "/v1/chat/completions", json=body, headers=headers
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    return
                choices = json.loads(data).get("choices") or [{}]
                if delta := (choices[0].get("delta") or {}).get("content"):
                    yield delta
