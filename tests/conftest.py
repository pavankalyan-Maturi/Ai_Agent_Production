from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from apps.api.main import create_app
from apps.api.settings import Settings
from apps.api.telemetry import build_tracer_provider


async def _ok(_: Settings) -> None:
    return None


async def _down(_: Settings) -> None:
    raise ConnectionRefusedError("postgresql://user:secret@db/x refused")


@pytest.fixture
def settings() -> Settings:
    return Settings(env="ci", readiness_timeout_s=0.5)


@pytest.fixture
def spans() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def make_client(settings: Settings, spans: InMemorySpanExporter):
    def _make(checks: dict[str, object] | None = None) -> TestClient:
        provider = build_tracer_provider(settings, processor=SimpleSpanProcessor(spans))
        app = create_app(settings, tracer_provider=provider, readiness_checks=checks)
        return TestClient(app)

    return _make


@pytest.fixture
def client(make_client) -> Iterator[TestClient]:
    with make_client({"postgres": _ok, "redis": _ok}) as c:
        yield c


__all__ = ["_down", "_ok"]
