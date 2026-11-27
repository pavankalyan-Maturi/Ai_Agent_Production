"""Per-key token-bucket rate limits in Redis (ADR 011).

One atomic Lua script per check: refill from Redis `TIME` (no client clock skew across replicas),
then take `cost` tokens or report how long until they exist. Bucket key carries the tenant id and
the caller key (OIDC `sub`), so tenants never share a bucket. Two buckets per request: requests
per minute (cost 1) and LLM tokens per minute (cost = estimated prompt + max output tokens).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# KEYS[1]=bucket; ARGV: capacity, refill_per_s, cost. Returns {allowed, retry_after_ms, left}.
_LUA = """
local cap = tonumber(ARGV[1]); local rate = tonumber(ARGV[2]); local cost = tonumber(ARGV[3])
local t = redis.call('TIME'); local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local s = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(s[1]) or cap; local ts = tonumber(s[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate / 1000)
local allowed = 0; local wait = 0
if cost > cap then
  wait = -1
elseif tokens >= cost then
  tokens = tokens - cost; allowed = 1
else
  wait = math.ceil((cost - tokens) * 1000 / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(cap * 1000 / rate) + 1000)
return {allowed, wait, math.floor(tokens)}
"""


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    retry_after_s: float  # 0 when allowed; inf when cost can never fit
    remaining: int
    limit: str = ""


@dataclass(frozen=True)
class Limit:
    name: str  # "rpm" | "tpm"
    per_minute: int

    @property
    def refill_per_s(self) -> float:
        return self.per_minute / 60.0


class RateLimiter:
    def __init__(self, redis: Any, limits: tuple[Limit, ...], prefix: str = "rl") -> None:
        self._redis = redis
        self._script = redis.register_script(_LUA)
        self.limits = {lim.name: lim for lim in limits}
        self.prefix = prefix

    def key(self, tenant: str, caller: str, limit: str) -> str:
        return f"{self.prefix}:{tenant}:{caller}:{limit}"

    async def take(self, tenant: str, caller: str, limit: str, cost: int = 1) -> Verdict:
        lim = self.limits[limit]
        allowed, wait_ms, left = await self._script(
            keys=[self.key(tenant, caller, limit)], args=[lim.per_minute, lim.refill_per_s, cost]
        )
        if int(wait_ms) < 0:
            return Verdict(False, math.inf, int(left), limit)
        return Verdict(bool(allowed), int(wait_ms) / 1000.0, int(left), limit)

    async def check(self, tenant: str, caller: str, costs: dict[str, int]) -> Verdict:
        """Take from each bucket in order; the first refusal wins. (A refused later bucket does
        not refund earlier ones: a client hammering past its token budget still burns requests.)"""
        last = Verdict(True, 0.0, 0)
        for name, cost in costs.items():
            last = await self.take(tenant, caller, name, cost)
            if not last.allowed:
                return last
        return last
