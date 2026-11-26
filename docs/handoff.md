# Handoff

## Done
- Phase 0 merged (PR #1). Phase 1A merged (PR #2): retrieval core + SciFact ablation, ADR 002.
- Phase 1B (this PR, not merged): S3 → RabbitMQ → Celery ingest in `apps/ingest/` (quorum queue + DLX → `ingest.dlq`, delivery limit 5, acks_late, full-jitter retries, poison → DLQ), idempotency ledger `ingest_jobs`, `POST /v1/ingest` (202, idempotent). Compose core profile gains rabbitmq, ingest-worker, s3. Dockerfile now ships `retrieval/` and fastembed. ADR 003.

## Verified here
- Gate: ruff, format, mypy strict, 37 unit tests; actionlint.
- Integration (real RabbitMQ 4.2 + pgvector + moto S3 + in-process worker): DLQ 4/4, dedupe 10/10, redelivery no-op, retry ×2 → done. 3 of 4 runs green (first run timed out, cause not captured).
- Image build stage: imports, task registered, `celery` binary present.

## Not verified here
- Final image stage (apt mirrors blocked here) and `compose-smoke` with the new core services: CI.
- Worker with the real fastembed model (needs HF access): you or CI.
- Integration test flakiness: CI.

## Open
- Stale chunks when a re-ingested document gets shorter (ADR 003).
- Tenant from header until auth (Phase 5). Text only (no PDF/HTML extraction).

## Next step
Review and merge this PR. Then Phase 2 (LangGraph agent loop) on request.

## Versions pinned
docs/SPEC.md Pins table, ADR 002, ADR 003 (rabbitmq 4.2, celery/kombu 5.7.x, boto3 1.44.x, moto 5.2.x).
