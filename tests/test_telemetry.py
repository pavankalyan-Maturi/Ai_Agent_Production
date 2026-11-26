from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_traced_route_emits_server_span(client, spans: InMemorySpanExporter) -> None:
    assert client.get("/v1/ping").status_code == 200
    server = [s for s in spans.get_finished_spans() if s.kind.name == "SERVER"]
    assert server, "expected a SERVER span for /v1/ping"
    assert server[0].resource.attributes["service.name"] == "pap-api"


def test_probes_are_not_traced(client, spans: InMemorySpanExporter) -> None:
    client.get("/healthz")
    client.get("/readyz")
    client.get("/metrics")
    assert spans.get_finished_spans() == ()


def test_metrics_use_route_template_labels(client) -> None:
    client.get("/v1/ping")
    client.get("/does-not-exist-123")
    body = client.get("/metrics").text
    assert 'pap_http_requests_total{method="GET",route="/v1/ping",status="200"}' in body
    assert "does-not-exist-123" not in body
    assert 'route="unmatched"' in body
