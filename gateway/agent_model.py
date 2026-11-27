"""Gateway-backed ChatModel for the agent graph (ADR 011, Phase 4 follow-up).

`GatewayModel` wraps any `ChatModel` (Anthropic, LiteLLM-routed, scripted) and puts the same
gateway policy the streaming endpoint uses in front of every model-node call:

1. Per-(tenant, caller) token buckets: rpm cost 1, tpm cost = len(json(view)) / 4 + max output.
   Shares bucket keys with `/v1/chat/stream`, so one caller cannot double its budget by mixing
   the agent and the raw chat route. A refusal waits up to `max_wait_s`, then raises.
2. Tenant-scoped exact cache of the whole `Decision` (text, tool calls, provider blocks) keyed
   on (model, transcript view, offered tools). A hit reports 0 tokens: nothing was spent.

No semantic cache here: agent transcripts carry tool traffic, which `semantic_query` refuses
by design. The model is bound to one tenant and caller at construction (one per run), because
`ChatModel.decide` carries no principal.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from agent.model import ChatModel, Decision, Message, ToolCall, ToolSpec
from gateway.ratelimit import SyncRateLimiter, Verdict

_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")  # same rule as apps.api.auth.TENANT_RE


class RateLimited(RuntimeError):
    def __init__(self, verdict: Verdict) -> None:
        super().__init__(f"rate limit {verdict.limit} exceeded, retry in {verdict.retry_after_s}s")
        self.verdict = verdict


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


@dataclass
class GatewayModel:
    inner: ChatModel
    redis: Any  # sync redis.Redis
    tenant: str
    caller: str
    limiter: SyncRateLimiter | None = None
    cache: bool = True
    ttl_s: int = 3600
    max_output_tokens: int = 1024
    max_wait_s: float = 0.0
    prefix: str = "llm:agent"
    sleep: Callable[[float], None] = time.sleep
    hits: int = field(default=0, init=False)
    misses: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not _TENANT.match(self.tenant):
            raise ValueError("invalid tenant id")
        self.name = self.inner.name

    def key(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> str:
        body = _canon([self.name, list(messages), [asdict(t) for t in tools]])
        return f"{self.prefix}:{self.tenant}:{hashlib.sha256(body.encode()).hexdigest()}"

    def _admit(self, messages: Sequence[Message]) -> None:
        if self.limiter is None:
            return
        want = {"rpm": 1, "tpm": len(_canon(list(messages))) // 4 + self.max_output_tokens}
        costs = {k: v for k, v in want.items() if k in self.limiter.limits}
        deadline = time.monotonic() + self.max_wait_s
        while True:
            v = self.limiter.check(self.tenant, self.caller, costs)
            if v.allowed:
                return
            if math.isinf(v.retry_after_s) or time.monotonic() + v.retry_after_s > deadline:
                raise RateLimited(v)
            self.sleep(v.retry_after_s)

    def decide(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Decision:
        self._admit(messages)
        k = self.key(messages, tools) if self.cache else ""
        if k and (raw := self.redis.get(k)):
            d = json.loads(raw)
            self.hits += 1
            calls = tuple(ToolCall(c["id"], c["name"], c["args"]) for c in d["calls"])
            return Decision(text=d["text"], tool_calls=calls, raw=d["raw"])
        self.misses += 1
        out = self.inner.decide(messages, tools)
        if k:
            payload: dict[str, Any] = {
                "text": out.text,
                "calls": [asdict(c) for c in out.tool_calls],
                "raw": out.raw,
                "in": out.input_tokens,
                "out": out.output_tokens,
            }
            self.redis.set(k, _canon(payload), ex=self.ttl_s)
        return out
