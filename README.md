# prod-agent-platform

A production-grade agentic RAG platform, built phase by phase with tests, a measured number and an ADR per phase.
Every claim here links to a CI run or a row in [docs/RESULTS.md](docs/RESULTS.md).

| Phase | Scope | Status |
|---|---|---|
| 0 | Skeleton: FastAPI, probes, OTel, Prometheus/Grafana/Loki, Langfuse v4, compose, CI | done |
| 1 | Ingestion (Celery+DLQ), contextual retrieval, hybrid pgvector+FTS, rerank, ablation | done |
| 2 | LangGraph agent, Postgres checkpointer, budgets, HITL, gated experience memory | done |
| 3 | Tool governance (mcp-rbac-gateway), MCP 2026-07-28 (stateless) server, A2A v1 card | in progress |
| 4 | LiteLLM gateway, rate limits, exact+semantic cache, SSE, Locust | todo |
| 5 | Guardrails: PII, injection suite, plan/data separation, step+trace checks | todo |
| 6 | Evals, judge calibration, MAST tagging, CI eval gate, GEPA | todo |
| 7 | vLLM serving benchmarks (human-run on rented GPU) | todo |
| 8 | AWS burst: Terraform, EKS, KEDA, Argo CD, restore drill (human-run) | todo |

## Security architecture: tool governance

Every tool call the agent makes passes through [mcp-rbac-gateway](https://github.com/pavankalyan-Maturi/mcp-rbac-gateway) (imported as `mcp_rbac_gateway`), configured by [`sentinel.yaml`](sentinel.yaml). Details are in [ADR 006](docs/adr/006-tool-governance.md).

```
model proposes a tool call
   │
   ▼
route ── needs approval per sentinel.yaml? ──► approve: LangGraph interrupt (Postgres checkpoint)
   │                                              │  resume {"approved", "approver"}
   ▼                                              ▼
act ── Governance.execute(role, tenant, args) ─────────────────────────────────────────┐
   │   1. policy: role allowlist, argument regex / length / allowed values             │
   │   2. input: secret redaction, prompt-injection scan                                │
   │   3. approval: the graph's approver, or /api/v1/security/approvals (broker)        │
   │   4. tool runs with the redacted arguments                                         │
   │   5. output: secret + PII redaction, prompt-injection score ≥ 0.7 → blocked         │
   │   6. audit row in SQLite: role, tool, decision, approver, tenant, call id, trace   │
   ▼                                                                                    │
observation for the model: the tool's redacted output, or a structured denial ◄─────────┘
  error: tool_denied {"tool", "decision", "reason", "retry": false, "hint"}
```

- **Roles.** Every run carries a `role` that the caller sets, as with `tenant_id`; the model never sets it.
  - `researcher` is read-only: search, fetch_doc and calculator.
  - `operator` adds `create_ticket`, which always needs a human.
  - With no role, every tool is denied (`default_action: deny`).
  - The model is only offered the tools its role allows.
- **Approvals.** The policy decides what needs a human, and the graph's checkpointed pause does the waiting, so a pending approval survives restarts. Calls made outside the graph wait on the gateway's approval broker.
- **API.** Both endpoints need `Authorization: Bearer $PAP_ADMIN_API_KEY` and are off (503) until it is set:
  - `GET /api/v1/security/approvals`, `POST …/{id}/approve|deny`
  - `GET /api/v1/security/audit?tenant_id=…`
- **Cost.** About 2 ms per tool call (RESULTS, Phase 3A).

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
