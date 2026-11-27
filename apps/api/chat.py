"""POST /v1/chat/stream: SSE chat through the LLM gateway (P4.2/P4.3, ADR 011).

Order per request: scope check (OIDC) -> model allowlist -> per-(tenant, sub) rate limits
(429 + Retry-After) -> tenant-scoped cache -> LiteLLM stream. Events: `meta` (cache result),
`delta` (text), `error`, `done`. Only a completed stream is cached. A client disconnect cancels
the response task; that unwinds `StreamClient.stream`, which closes the upstream connection.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from prometheus_client import Counter
from pydantic import BaseModel, Field

from apps.api.auth import Chatter
from gateway.cache import LLMCache
from gateway.client import StreamClient
from gateway.ratelimit import Limit, RateLimiter

log = logging.getLogger(__name__)
router = APIRouter()

CACHE_RESULTS = Counter("pap_llm_cache_total", "LLM cache lookups", ["result"])
RATE_LIMITED = Counter("pap_rate_limited_total", "Requests refused by rate limit", ["limit"])
STREAMS = Counter("pap_chat_streams_total", "Chat streams by outcome", ["outcome"])


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=32_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    model: str | None = None
    max_tokens: int = Field(default=1024, ge=1, le=4096)


@dataclass
class ChatDeps:
    limiter: RateLimiter
    cache: LLMCache
    upstream: StreamClient


def build_deps(settings: Any) -> Callable[[], ChatDeps]:
    cache: list[ChatDeps] = []

    def get() -> ChatDeps:
        if not cache:
            import redis.asyncio as aioredis

            r = aioredis.from_url(str(settings.redis_url))
            embedder = None
            if settings.cache_semantic:
                try:
                    from retrieval.embedding import FastEmbedEmbedder

                    embedder = FastEmbedEmbedder()
                except ImportError:
                    log.warning("fastembed not installed: semantic cache off, exact cache only")
            key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
            http = httpx.AsyncClient(base_url=settings.llm_gateway_url, timeout=60.0)
            cache.append(
                ChatDeps(
                    RateLimiter(
                        r,
                        (
                            Limit("rpm", settings.rate_limit_rpm),
                            Limit("tpm", settings.rate_limit_tpm),
                        ),
                    ),
                    LLMCache(
                        r,
                        embedder,
                        ttl_s=settings.cache_ttl_s,
                        threshold=settings.cache_semantic_threshold,
                    ),
                    StreamClient(http, key),
                )
            )
        return cache[0]

    return get


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/v1/chat/stream")
async def chat_stream(body: ChatRequest, request: Request, who: Chatter) -> StreamingResponse:
    s = request.app.state.settings
    model = body.model or s.llm_model
    if model not in s.llm_models:
        raise HTTPException(400, f"unknown model {model!r}")
    deps: ChatDeps = request.app.state.chat_deps()
    messages = [m.model_dump() for m in body.messages]
    est_tokens = sum(len(m["content"]) for m in messages) // 4 + body.max_tokens
    v = await deps.limiter.check(who.tenant_id, who.sub, {"rpm": 1, "tpm": est_tokens})
    if not v.allowed:
        RATE_LIMITED.labels(v.limit).inc()
        retry = "3600" if math.isinf(v.retry_after_s) else str(max(1, math.ceil(v.retry_after_s)))
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": retry})

    params = {"max_tokens": body.max_tokens}
    hit = await deps.cache.get(who.tenant_id, model, messages, params)
    CACHE_RESULTS.labels(hit.kind if hit else "miss").inc()

    async def events() -> AsyncIterator[str]:
        if hit is not None:
            yield _sse("meta", {"cache": hit.kind, "model": model})
            yield _sse("delta", {"text": hit.text})
            yield _sse("done", {})
            STREAMS.labels("cached").inc()
            return
        yield _sse("meta", {"cache": "miss", "model": model})
        parts: list[str] = []
        try:
            async for delta in deps.upstream.stream(model, messages, body.max_tokens):
                parts.append(delta)
                yield _sse("delta", {"text": delta})
        except httpx.HTTPError as e:
            STREAMS.labels("upstream_error").inc()
            log.warning("upstream stream failed: %s", type(e).__name__)
            yield _sse("error", {"error": "upstream unavailable"})
            return
        except BaseException:  # client disconnect -> CancelledError; upstream closed on unwind
            STREAMS.labels("cancelled").inc()
            raise
        text = "".join(parts)
        await deps.cache.put(who.tenant_id, model, messages, params, text, 0, len(parts))
        STREAMS.labels("completed").inc()
        yield _sse("done", {})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )
