# prod-agent-platform: SPEC (v1)

## Goal
Production-grade agentic RAG platform with measured quality, cost, latency and security.
Every claim in README links to a CI run or a RESULTS.md row.

## Non-goals
Chaos engineering beyond one Chaos Mesh experiment; RL training; multi-tenant SaaS billing.

## Repo layout (created as phases land)
```
apps/api/            FastAPI app (routes, SSE, auth, rate limits)
apps/worker/         Celery ingestion workers (RabbitMQ, DLQ)
agent/               LangGraph graph: planner, tools, budgets, HITL, memory
retrieval/           chunking, contextual retrieval, hybrid (pgvector + FTS), rerank
memory/              experience memory (ReasoningBank-style) + admission gate
guardrails/          PII, injection, plan/data separation, step + trace checks
gateway/             LiteLLM config, exact + semantic cache, fallbacks
mcp_server/          MCP 2026-07-28 server (stateless, Streamable HTTP)
a2a/                 A2A 1.x agent card + JSON-RPC binding
evals/               dataset loaders, trajectory evals, judge calibration, MAST tagging, gate
serving/             vLLM configs, benchmark scripts (Phase 7)
deploy/compose/      docker-compose.yml with profiles core, obs, llmobs, full
deploy/helm/, deploy/kind/, deploy/terraform/
loadtest/            Locust
docs/                SPEC, TASKS, handoff, RESULTS, COSTS, adr/, runbooks/
.github/workflows/   ci, compose-smoke, security (+ kind-e2e, eval-gate later)
```

## Pins (binding; a change needs an ADR). "installed" = in uv.lock / compose today.
| Component | Pin | State |
|---|---|---|
| Python | 3.12 | installed |
| FastAPI / Uvicorn / Pydantic / pydantic-settings | 0.165.x / 0.54.x / 2.13.x / 2.16.x | installed |
| OpenTelemetry SDK + OTLP HTTP exporter | 1.45.x; instrumentation-fastapi 0.66b2 | installed |
| OTel GenAI semconv | schema 1.44.0 via opentelemetry-semantic-conventions==0.66b2 (Development status; ADR 005) | installed |
| Postgres + pgvector | postgres 17 + pgvector 0.8.6 (`pgvector/pgvector:0.8.6-pg17`) | installed |
| PgBouncer | 1.24.1 (transaction pooling, no prepared statements) | installed |
| Redis | 7.4 | installed |
| OTel Collector contrib / Prometheus / Grafana / Loki | 0.140.0 / 3.7.3 / 12.2.1 / 3.5.7 | installed |
| Langfuse | v4 (image tag `4`, observed 4.88.0) + ClickHouse 25.12 | installed |
| S3-compatible store | SeaweedFS 4.01 (MinIO images unavailable, ADR 001) | installed |
| LangGraph | langgraph==1.2.24, checkpoint==4.3.2, checkpoint-postgres==3.2.1, prebuilt==1.1.3; psycopg 3 | installed (ADR 004) |
| MCP | spec 2026-07-28 (stateless); `mcp[cli]==2.3.*` (MCPServer) | installed 2.3.0 (ADR 007: was 2.0.*, moved for 5 CVEs) |
| A2A | spec 1.0; `a2a-sdk>=1.1,<2` | installed 1.3.1 (ADR 008) |
| Tool governance | `mcp-rbac-gateway>=0.2.0,<0.3` (import `mcp_rbac_gateway`), policy `sentinel.yaml` | installed (ADR 006) |
| LiteLLM proxy | `litellm[proxy]==1.110.0` (own image, uid 10001, admin routes blocked); never 1.107.0 | built P4 (ADR 011) |
| Load test | `locust==2.47.0` via `uvx` (not a project dependency) | P4 |
| RabbitMQ / Celery / kombu | rabbitmq 4.2 (`rabbitmq:4.2-management-alpine`, quorum queues) / celery 5.7.x / kombu 5.7.x (ADR 003) | installed |
| S3 client | boto3 1.44.x; tests: moto 5.2.x server mode | installed |
| Keycloak | `quay.io/keycloak/keycloak:26.4.2`, compose profile `auth`, dev mode (ADR 009) | compose |
| vLLM | 0.34.0 (Phase 7, human-run), Llama-3.1-8B-Instruct + EAGLE-3 head | ADR 013 |
| Burst IaC | Terraform 1.13.4, hashicorp/aws 6.20.0, EKS 1.34 (standard support), k3s v1.34.1+k3s1 | code only (ADR 014) |
| Burst add-ons | LBC chart 3.2.0, metrics-server 3.13.0, KEDA 2.18.1, Argo CD chart 9.1.0, Chaos Mesh 2.7.3, Crunchy PGO 5.8.2 (UNVERIFIED) | code only (ADR 014) |

## Models (Claude via LiteLLM; one place to change)
- Default generation: `claude-sonnet-5-5`. Cheap bulk work (contextual chunk headers, judges after calibration): `claude-haiku-5-5`.
- Prompt caching on every repeated prefix (per-document context for contextual retrieval).
- CI uses a deterministic mock model behind the same LiteLLM interface; real-model runs are opt-in and cost-capped.

## Feature flags (env, default)
RETRIEVAL_CONTEXTUAL=off (ADR 002), RETRIEVAL_LATE_CHUNKING=off, RETRIEVAL_RERANK=cross_encoder|off,
RETRIEVAL_GRAPH_HIPPORAG=off, PGVECTOR_ITERATIVE_SCAN=relaxed_order,
MEMORY_EXPERIENCE=off|reasoningbank, MEMORY_ADMISSION_GATE=on, REFLECTION=grounded_only,
CONTEXT_COMPACTION=on, TOOL_RETRIEVAL_RAGMCP=off, GUARD_PLAN_DATA_SEPARATION=on,
GUARD_STEP_CHECK=on, CACHE_SEMANTIC=on, SPEC_DECODING=off

## Datasets (public; small CI slices + full offline runs)
- Retrieval: BEIR SciFact and FiQA (CI slice: 200 queries)
- Multi-hop/agentic: HotpotQA and MuSiQue dev subsets (CI: 100 questions)
- Injection/security: AgentDojo suites (offline) + in-repo injected-document set (CI: 50 cases)
- Judge calibration: 100 hand-labelled items in evals/labels/ (human-written, never generated)

## Acceptance tests per phase (CI job in brackets)
- P0 skeleton: compose core profile boots; /healthz, /readyz; OTel traces exported; ruff+mypy+pytest green [ci, compose-smoke]
- P1 retrieval: ingestion via Celery with DLQ on poison docs; hybrid+rerank; ablation table (naive -> contextual -> +BM25 -> +rerank) with recall@20/nDCG@10 on SciFact slice in RESULTS.md; filtered-search recall test with iterative scan [ci, eval-gate:retrieval]
- P2 agent: Postgres checkpointer survives worker kill mid-run; step/token/$ budgets enforced; HITL interrupt/approve; experience memory with admission gate; ablation memory off/on/gated on HotpotQA slice [ci, eval-gate:agent]
- P3 protocols: MCP server passes SDK-v2 Client (mode=auto) tests for 2026-07-28 and 2025-11-25; two replicas behind round-robin succeed without session store; A2A card at /.well-known/agent-card.json, JWS-signed, verified by test client [ci, kind-e2e]
- P4 gateway: per-key rate limits; exact + semantic cache hit-rate and false-hit test; provider fallback on injected 5xx; SSE with client-disconnect handling; Locust p50/p95 and cache savings [compose-smoke, loadtest]
- P5 guardrails: PII redaction tests; injection suite attack-success-rate before/after; plan/data separation blocks exfil sink; false-block rate and latency overhead [eval-gate:security]
- P6 evals: trajectory evals; judge kappa vs human labels >= 0.6 before the gate is enforced; MAST failure histogram; GEPA vs MIPROv2 vs baseline on one module; eval-gate blocks PR on regression beyond CI bound [eval-gate]
- P7 serving (human-run): vLLM 0.34 OpenAI-compatible endpoint via LiteLLM; prefix caching and EAGLE-3 on/off benchmark (TTFT, TPOT, tok/s at batch 1/8/32)
- P8 cloud burst (human-run): terraform plan validated in CI; apply, Argo CD sync, KEDA scale-out under Locust, pgBackRest restore drill (RTO measured), destroy; evidence committed

## Budget (Claude usage, USD, estimates)
P0 6 | P1 14 | P2 16 | P3 10 | P4 10 | P5 10 | P6 12 | P7 6 | P8 6 | reserve 10 (total 100)

## Cut list (in order)
1. Late chunking, HippoRAG flag, ColPali  2. GEPA  3. A2A signing  4. Disaggregated prefill/decode, KV offload  5. EKS (kind or k3s-on-EC2 with recorded evidence)
Never cut: tests, CI eval gate, RESULTS.md honesty, guardrail evals.
