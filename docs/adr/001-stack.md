# ADR 001: Phase 0 runtime and observability stack

Status: proposed (Phase 0 PR) · Date: 2026-11-26

## Context
Phase 0 needs a service skeleton whose probes, traces and metrics are real from day one, so every later
phase can attach numbers to the same pipeline. Constraints: local and free (Tier 1), no secrets in code,
PII off in traces, everything verifiable inside a CI runner.

## Decisions
1. **FastAPI + Uvicorn, app factory (`uvicorn --factory apps.api.main:create_app`).** No import-time side effects,
   so tests build an app with an in-memory span exporter and fake readiness checks. Alternative: module-level
   `app` (simpler, but sets global tracer state on import and makes tests order-dependent).
2. **Probes split by purpose.** `/healthz` touches nothing (liveness must not flap when Postgres blips, or K8s
   restarts healthy pods). `/readyz` checks Postgres *through PgBouncer* and Redis concurrently, each under a
   timeout, and returns 503 with per-check status. Errors report the exception type only: messages can carry DSNs.
3. **PgBouncer in transaction mode from day one**, with `prepare_threshold=None` in psycopg. This is the setting
   that breaks LangGraph's checkpointer later if forgotten, so it is exercised now.
4. **Metrics: prometheus-client with route-template labels**, not raw paths, so a scanner hitting random URLs
   cannot blow up cardinality (tested). Alternative: OTel metrics SDK -> collector -> Prometheus; rejected for
   now as an extra hop with no gain for a single service. Revisit when workers exist.
5. **Traces: OTel SDK -> OTLP/HTTP -> Collector -> Langfuse v4 OTLP endpoint.** The collector strips
   `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions` and auth headers as defence
   in depth for the PII rule; the app also defaults `otel_capture_content=false`. Probes and /metrics are
   excluded from tracing (noise).
6. **Compose profiles `core`, `obs`, `llmobs`, `full`.** Langfuse v4 needs ClickHouse + its own Redis
   (`noeviction`) + S3, the heaviest part of the stack, so it is a separate profile. The SPEC's original
   layout put Langfuse in `obs`; split here so `obs` stays cheap on small machines. compose-smoke still boots it.
7. **S3: SeaweedFS 4.01 instead of MinIO.** `minio/minio` could not be pulled from Docker Hub in this
   environment (repository no longer published), and quay.io/cgr.dev were unreachable. SeaweedFS is
   Apache-2.0 and S3-compatible; Langfuse event upload works against it (verified). Revisit if the team
   prefers MinIO from a source build.
8. **Separate Redis for Langfuse.** Langfuse requires `noeviction`; the app's Redis will hold caches in Phase 4
   that should use an LRU policy. Sharing one instance would force the wrong policy on one side.
9. **Container image: python:3.12-slim + `apt-get upgrade` for Debian security fixes, uv-built venv, non-root user, HEALTHCHECK.** Optional proxy CA is a
   BuildKit secret, never a layer.

## Consequences
- Langfuse v4 is `events_only`: the legacy `/api/public/traces` read API is gone; use
  `/api/public/v2/observations`. The smoke test does.
- `/readyz` opens a fresh Postgres connection per call (p50 about 10 ms locally). Acceptable at probe rates;
  revisit with a pool once the app holds one (Phase 1).

## Versions pinned here
See SPEC.md Pins table (rows marked installed). GitHub Actions are pinned by major tag
(`actions/checkout@v5`, `astral-sh/setup-uv@v7`, `docker/build-push-action@v6`,
Trivy runs from the pinned `aquasec/trivy:0.69.3` image instead of trivy-action, whose guessed tag did not exist). Pinning by SHA
is a follow-up.

## Not verified in this environment
- GitHub Actions runs (first run happens on the PR).
- Langfuse on a 2-vCPU private-repo runner; it booted in 25 s on a 4-vCPU/15 GB sandbox.
