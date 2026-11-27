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
- P3.0 tool governance with mcp-rbac-gateway (owner request): RBAC roles, arg constraints, redaction, injection blocking, approvals + audit API — DONE (PR #6, ADR 006)
- P3.1 MCPServer (stateless, Streamable HTTP), tools = retrieval/search; OAuth per spec, ADR — DONE (PR #7, ADR 007; behind mcp-rbac-gateway StatelessHttpProxy)
- P3.2 conformance tests with SDK v2 Client both eras; 2-replica kind test — DONE (PR #7: both eras + in-process 2-replica round robin; kind e2e NOT RUN)
- P3.3 tool-retrieval (RAG-MCP) flag + tool-count scaling eval — IN REVIEW (PR C, ADR 008; task success with a real model NOT MEASURED)
- P3.4 A2A card (supported_interfaces, skills, securityRequirements), JWS signing + verification test — IN REVIEW (PR C, ADR 008)

## Phase 4: Gateway  [CHECKPOINT: load-test review]
- P4.1 LiteLLM proxy hardened (non-root, admin UI off, risky routes blocked) + ADR with CVE list — IN REVIEW (ADR 011)
- P4.2 per-key rate limits (Redis), exact + semantic cache with false-hit test, fallbacks — IN REVIEW
- P4.3 SSE streaming endpoint; disconnect/cancel propagation — IN REVIEW
- P4.4 Locust scenario; p50/p95, cache savings -> RESULTS.md — IN REVIEW (local run; compose run is yours)

## Phase 5: Guardrails  [CHECKPOINT: security results review]
- P5.0 OIDC auth (Keycloak RS, scopes, tenant from token) replacing the admin key — IN REVIEW (PR A, ADR 009; Keycloak compose profile `auth` added in the Phase 4 follow-up PR)
- P5.1 PII detect/redact in/out — IN REVIEW (PR A)
- P5.2 injected-doc suite + spotlighting — IN REVIEW (PR A; model ASR from CI eval-security)
- P5.3 plan/data separation + taint tags — IN REVIEW (PR B, ADR 010)
- P5.4 step-level tool-call checker + trace-level policy; latency + false-block metrics — IN REVIEW (PR B; Haiku numbers from CI)

## Phase 6: Evals + CI gate  [CHECKPOINT: human labels + threshold sign-off]
- P6.1 trajectory evals — DONE (PR #14, ADR 012)
- P6.2 judge calibration (kappa) vs evals/labels/ — DONE (kappa 0.827 vs 100 human labels, PR #16/#17)
- P6.3 MAST tagging + histogram — DONE (PR #14; rule-based, 6 of 14 modes, UNVERIFIED vs human tags)
- P6.4 eval-gate.yml with bootstrap CI and cost cap — DONE (PR #14-#17; f1 + judge + cost cap enforced, signed off)
- P6.5 GEPA vs MIPROv2 on synthesis module — CUT (user decision 2026-11-27, SPEC cut list #2; ADR 012)

## Phase 7: vLLM (human-run)  [CHECKPOINT: before renting GPU]
- P7.1 serving/ configs + bench scripts [DONE, PR]; P7.2 runbook (RunPod L40S/H100), cost estimate, teardown [DONE, PR: docs/runbooks/vllm.md]; P7.3 human runs, logs -> RESULTS.md [OPEN, human]

## Phase 8: Cloud burst (human-run)  [CHECKPOINT: before terraform apply]
- P8.1 terraform (EKS, 1 AZ) + k3s-on-EC2 alternative; terraform validate + tflint in CI [DONE, PR; not applied]
- P8.2 Helm, Argo CD app, KEDA scaler, Chaos Mesh experiment, pgBackRest restore drill runbook [DONE, PR: docs/runbooks/aws-burst.md]
- P8.3 destroy script + bill check [DONE, PR]; human runs; evidence committed [OPEN, human, needs AWS budget]
