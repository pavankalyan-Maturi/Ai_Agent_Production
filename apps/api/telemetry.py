"""OpenTelemetry tracing + Prometheus metrics wiring."""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from prometheus_client import Counter, Histogram

from apps.api.settings import Settings

HTTP_REQUESTS = Counter("pap_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "pap_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# Routes whose spans add noise and no signal.
EXCLUDED_URLS = "healthz,readyz,metrics"


def build_tracer_provider(
    settings: Settings, exporter: SpanExporter | None = None, processor: SpanProcessor | None = None
) -> TracerProvider:
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": settings.service_name, "deployment.environment.name": settings.env}
        )
    )
    if processor is not None:
        provider.add_span_processor(processor)
    elif exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    elif settings.otel_exporter_otlp_endpoint:
        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return provider


def instrument(app: FastAPI, provider: TracerProvider) -> None:
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=EXCLUDED_URLS)
