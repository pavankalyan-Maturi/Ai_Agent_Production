"""OpenAI-compatible mock upstream: stands in for vLLM/Claude behind LiteLLM in CI and load tests.

`uvicorn gateway.mock_llm:app --port 8010`. Models: `mock` answers deterministically,
`mock-flaky` always returns 503 (to exercise LiteLLM fallbacks), `mock-slow` streams slowly
(to exercise client-disconnect propagation). MOCK_LATENCY_S adds per-request latency.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="mock-llm")
app.state.stats = {"requests": 0, "streams_started": 0, "streams_cancelled": 0}


class ChatBody(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    tools: list[dict[str, Any]] | None = None


def _answer(messages: list[dict[str, Any]]) -> str:
    last: dict[str, Any] = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    content = last.get("content", "")
    text = content if isinstance(content, str) else json.dumps(content)
    return f"mock answer ({len(text.split())} words): {text[:80]}"


def _usage(messages: list[dict[str, Any]], answer: str) -> dict[str, int]:
    p = sum(len(str(m.get("content", "")).split()) for m in messages)
    c = len(answer.split())
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, **app.state.stats}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    ids = ("mock", "mock-flaky", "mock-slow")
    return {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}


@app.post("/v1/chat/completions", response_model=None)
async def chat(body: ChatBody) -> dict[str, Any] | StreamingResponse:
    app.state.stats["requests"] += 1
    model = body.model.split("/")[-1]
    if model.startswith("mock-flaky"):
        raise HTTPException(503, "injected upstream failure")
    if delay := float(os.environ.get("MOCK_LATENCY_S", "0")):
        await asyncio.sleep(delay)
    answer = _answer(body.messages)
    created, rid = int(time.time()), f"chatcmpl-mock-{app.state.stats['requests']}"
    if not body.stream:
        return {
            "id": rid,
            "object": "chat.completion",
            "created": created,
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": _usage(body.messages, answer),
        }
    pause = 0.2 if model.startswith("mock-slow") else 0.0

    async def gen() -> AsyncIterator[str]:
        app.state.stats["streams_started"] += 1
        try:
            for word in answer.split(" "):
                chunk = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": body.model,
                    "choices": [{"index": 0, "delta": {"content": word + " "}}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(pause)
            yield "data: [DONE]\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            app.state.stats["streams_cancelled"] += 1
            raise

    return StreamingResponse(gen(), media_type="text/event-stream")
