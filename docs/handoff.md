# Handoff

## Done (Phase 0, PR open, not merged)
- FastAPI skeleton with app factory; /healthz, /readyz (PG via PgBouncer + Redis, timeouts, 503), /metrics, /v1/ping.
- OTel tracing (OTLP/HTTP) and Prometheus metrics with route-template labels; PII content attributes stripped at the collector.
- Compose profiles core / obs / llmobs / full; Grafana dashboard provisioned; Langfuse v4 receives spans.
- CI: ci.yml, compose-smoke.yml, security.yml. Docs: SPEC, TASKS, CLAUDE.md, ADR 001, RESULTS, COSTS.

## Verified here
pytest (11), ruff, mypy strict, actionlint, pip-audit, image build, full compose boot and smoke script incl. Langfuse.

## Not verified here (you or CI)
- First GitHub Actions run of all three workflows (action tags, runner size for ClickHouse).
- Trivy image scan with the apt-get upgrade fix: Debian mirrors are blocked in this sandbox, so only CI can confirm it.

## Open
- MinIO replaced by SeaweedFS (ADR 001 §7). Say if you want MinIO back.
- Actions pinned by tag, not SHA.

## Next step
Review and merge the Phase 0 PR, then send: **"Run Phase 1"** (ingestion, contextual retrieval, hybrid search, rerank, SciFact ablation).
Phase 1 will need an Anthropic API key as an environment network secret for contextual chunk headers (Haiku 5.5, prompt-cached); CI will use the mock model.

## Versions pinned
See docs/SPEC.md Pins table.
