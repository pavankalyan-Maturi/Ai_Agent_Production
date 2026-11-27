# TASKS (status: TODO | DOING | REVIEW | DONE | BLOCKED)
DONE requires the acceptance test to pass in CI (link it).

## Phase 0: Skeleton  [CHECKPOINT: human review of layout, CI, compose]
- P0.1 uv project, ruff, mypy (strict), pytest, pre-commit (local only; CI enforces) — REVIEW
- P0.2 FastAPI app, pydantic-settings, /healthz /readyz (+ /metrics, /v1/ping) — REVIEW
- P0.3 compose profiles core (postgres+pgvector, pgbouncer, redis, api), obs (otel-collector, prometheus, grafana, loki), llmobs (langfuse v4, clickhouse, redis, S3), full — REVIEW
- P0.4 OTel tracing + Prometheus metrics; Grafana dashboard JSON provisioned — REVIEW
- P0.5 GitHub Actions: ci.yml, compose-smoke.yml (core+obs+llmobs), security.yml (pip-audit, trivy image + config) — REVIEW (first CI run on the PR)
- P0.6 docs: handoff.md, RESULTS.md, COSTS.md, adr/001-stack.md — REVIEW
Acceptance: CI green; compose-smoke boots obs and Langfuse /api/public/health returns OK and receives spans.

## Phase 1: Ingestion + hybrid retrieval  [CHECKPOINT: ablation table review]
- P1.1 S3 upload -> RabbitMQ -> Celery ingest; retries, DLQ, idempotency key — DONE (PR #3)
- P1.2 chunker + contextual retrieval (prompt-cached, Haiku 5.5), embeddings via LiteLLM or local CPU model in CI — DONE (PR #2; contextual measured, no gain on SciFact, off by default)
- P1.3 hybrid search: pgvector HNSW + Postgres FTS, RRF fusion, iterative scan for tenant filters — DONE (PR #2)
- P1.4 reranker (small cross-encoder on CPU in CI) — DONE (PR #2)
- P1.5 evals/retrieval: BEIR SciFact slice; ablation ladder -> RESULTS.md — DONE (PR #2)
Acceptance: DLQ test, filtered-recall test, ablation row per config with CI link.

## Phase 2: Agent graph  [CHECKPOINT: memory ablation + budget policy review]
- P2.1 LangGraph planner/executor; tools: search, fetch_doc, calculator — DONE (PR #4)
- P2.2 AsyncPostgresSaver; crash-resume test — DONE (PR #4)
- P2.3 budgets (steps, tokens, USD) + graceful degrade; HITL interrupt for flagged tools — DONE (PR #4)
- P2.4 context compaction (tool-result truncation, summarize-at-threshold); token chart — IN REVIEW (PR B)
- P2.5 experience memory (ReasoningBank-style) + admission gate (replay with/without) — IN REVIEW (PR B)
- P2.6 grounded reflection only on external failure signals — IN REVIEW (PR B)

## Phase 3: MCP + A2A  [CHECKPOINT: protocol conformance review]
- P3.0 tool governance with mcp-rbac-gateway (owner request): RBAC roles, arg constraints, redaction, injection blocking, approvals + audit API — IN REVIEW (PR A, ADR 006)
- P3.1 MCPServer (stateless, Streamable HTTP), tools = retrieval/search; OAuth per spec, ADR
- P3.2 conformance tests with SDK v2 Client both eras; 2-replica kind test
- P3.3 tool-retrieval (RAG-MCP) flag + tool-count scaling eval
- P3.4 A2A card (supported_interfaces, skills, securityRequirements), JWS signing + verification test

## Phase 4: Gateway  [CHECKPOINT: load-test review]
- P4.1 LiteLLM proxy hardened (non-root, admin UI off, risky routes blocked) + ADR with CVE list
- P4.2 per-key rate limits (Redis), exact + semantic cache with false-hit test, fallbacks
- P4.3 SSE streaming endpoint; disconnect/cancel propagation
- P4.4 Locust scenario; p50/p95, cache savings -> RESULTS.md

## Phase 5: Guardrails  [CHECKPOINT: security results review]
- P5.1 PII detect/redact in/out; P5.2 injected-doc suite; P5.3 plan/data separation + taint tags
- P5.4 step-level tool-call checker + trace-level policy; latency + false-block metrics

## Phase 6: Evals + CI gate  [CHECKPOINT: human labels + threshold sign-off]
- P6.1 trajectory evals; P6.2 judge calibration (kappa) vs evals/labels/; P6.3 MAST tagging + histogram
- P6.4 eval-gate.yml with bootstrap CI and cost cap; P6.5 GEPA vs MIPROv2 on synthesis module

## Phase 7: vLLM (human-run)  [CHECKPOINT: before renting GPU]
- P7.1 serving/ configs + bench scripts; P7.2 runbook (RunPod L40S/H100), cost estimate, teardown; P7.3 human runs, logs -> RESULTS.md

## Phase 8: Cloud burst (human-run)  [CHECKPOINT: before terraform apply]
- P8.1 terraform (EKS, 1 AZ) + k3s-on-EC2 alternative; terraform validate + tflint in CI
- P8.2 Helm, Argo CD app, KEDA scaler, Chaos Mesh experiment, pgBackRest restore drill runbook
- P8.3 destroy script + bill check; human runs; evidence committed
