# prod-agent-platform

A production-grade agentic RAG platform, built phase by phase with tests, a measured number and an ADR per phase.
Every claim here links to a CI run or a row in [docs/RESULTS.md](docs/RESULTS.md).

| Phase | Scope | Status |
|---|---|---|
| 0 | Skeleton: FastAPI, probes, OTel, Prometheus/Grafana/Loki, Langfuse v4, compose, CI | in review |
| 1 | Ingestion (Celery+DLQ), contextual retrieval, hybrid pgvector+FTS, rerank, ablation | todo |
| 2 | LangGraph agent, Postgres checkpointer, budgets, HITL, gated experience memory | todo |
| 3 | MCP 2026-07-28 (stateless) server, A2A v1 card | todo |
| 4 | LiteLLM gateway, rate limits, exact+semantic cache, SSE, Locust | todo |
| 5 | Guardrails: PII, injection suite, plan/data separation, step+trace checks | todo |
| 6 | Evals, judge calibration, MAST tagging, CI eval gate, GEPA | todo |
| 7 | vLLM serving benchmarks (human-run on rented GPU) | todo |
| 8 | AWS burst: Terraform, EKS, KEDA, Argo CD, restore drill (human-run) | todo |

## Run it
```bash
uv sync --frozen && uv run pytest -q
cp .env.example .env
docker build -t pap-api:dev .
scripts/compose_smoke.sh            # core + obs
scripts/compose_smoke.sh --llmobs   # + Langfuse v4 (needs ~4 GB RAM free)
```
API :8000, Grafana :3001, Prometheus :9090, Langfuse :3000 (all bound to 127.0.0.1).

Spec: [docs/SPEC.md](docs/SPEC.md) · Tasks: [docs/TASKS.md](docs/TASKS.md) · ADRs: [docs/adr](docs/adr)
