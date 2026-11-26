"""Liveness and readiness. /healthz never touches dependencies; /readyz checks them all."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import psycopg
import redis.asyncio as aioredis
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from apps.api.settings import Settings

Check = Callable[[Settings], Awaitable[None]]

router = APIRouter()


class CheckResult(BaseModel):
    ok: bool
    latency_ms: float
    error: str | None = None


class Readiness(BaseModel):
    ready: bool
    checks: dict[str, CheckResult]


async def check_postgres(settings: Settings) -> None:
    # prepare_threshold=None: PgBouncer transaction pooling cannot hold prepared statements.
    async with await psycopg.AsyncConnection.connect(
        str(settings.database_url),
        autocommit=True,
        prepare_threshold=None,
        connect_timeout=max(1, int(settings.readiness_timeout_s)),
    ) as conn:
        await conn.execute("SELECT 1")


async def check_redis(settings: Settings) -> None:
    client = aioredis.from_url(str(settings.redis_url), socket_timeout=settings.readiness_timeout_s)
    try:
        await client.ping()
    finally:
        await client.aclose()


DEFAULT_CHECKS: dict[str, Check] = {"postgres": check_postgres, "redis": check_redis}


async def _run(name: str, check: Check, settings: Settings) -> tuple[str, CheckResult]:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(check(settings), timeout=settings.readiness_timeout_s)
        return name, CheckResult(ok=True, latency_ms=(time.perf_counter() - start) * 1000)
    except Exception as exc:  # report, never raise, from a probe
        return name, CheckResult(
            ok=False,
            latency_ms=(time.perf_counter() - start) * 1000,
            error=type(exc).__name__,  # type only: messages can carry DSNs
        )


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=Readiness)
async def readyz(request: Request, response: Response) -> Readiness:
    settings: Settings = request.app.state.settings
    checks: dict[str, Check] = request.app.state.readiness_checks
    results = dict(await asyncio.gather(*(_run(n, c, settings) for n, c in checks.items())))
    ready = all(r.ok for r in results.values())
    if not ready:
        response.status_code = 503
    return Readiness(ready=ready, checks=results)
