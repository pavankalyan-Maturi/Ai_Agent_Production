"""FastAPI entrypoint: `uvicorn --factory apps.api.main:create_app`."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.routing import Match

from apps.api import health
from apps.api.settings import Settings, get_settings
from apps.api.telemetry import HTTP_LATENCY, HTTP_REQUESTS, build_tracer_provider, instrument


def _route_template(request: Request) -> str:
    # Label by route template, never raw path, to keep metric cardinality bounded.
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match is Match.FULL:
            return str(getattr(route, "path", "unmatched"))
    return "unmatched"


def create_app(
    settings: Settings | None = None,
    tracer_provider: TracerProvider | None = None,
    readiness_checks: dict[str, health.Check] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    provider = tracer_provider or build_tracer_provider(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        provider.shutdown()  # flush pending spans

    app = FastAPI(title="prod-agent-platform", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.readiness_checks = (
        health.DEFAULT_CHECKS if readiness_checks is None else readiness_checks
    )
    app.include_router(health.router)

    @app.middleware("http")
    async def prometheus_mw(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        route = _route_template(request)
        start = time.perf_counter()
        response = await call_next(request)
        HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - start)
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/ping")
    async def ping() -> dict[str, str]:
        """Traced no-op route used by compose-smoke to prove span export end to end."""
        return {"pong": settings.service_name}

    instrument(app, provider)
    return app
