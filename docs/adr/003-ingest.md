# ADR 003: Ingestion pipeline (Phase 1, P1.1)

Status: accepted. Date: 2026-11-26.

## Decision

Client uploads to S3, then calls `POST /v1/ingest` (`X-Tenant-Id`, optional `Idempotency-Key`; body bucket, key, etag). The API writes a row to `ingest_jobs` and publishes a Celery task to RabbitMQ. A worker fetches the object, chunks, embeds and upserts into `chunks` (ADR 002).

- Broker: RabbitMQ 4.2, quorum queue `ingest` with `x-dead-letter-exchange=ingest.dlx` → quorum queue `ingest.dlq`, and `x-delivery-limit=5` so a message that keeps killing workers is dead-lettered instead of looping. Workers consume only `ingest` (`-Q ingest`); the DLQ is declared on worker start and is for humans (management UI).
- Celery 5.7: `acks_late`, `reject_on_worker_lost`, prefetch 1, JSON only, no result backend (state lives in Postgres).
- Failure classes:
  - Poison (missing object, > `PAP_INGEST_MAX_BYTES`, not UTF-8, empty) and unknown exceptions: job marked `failed`, message rejected without requeue → DLQ. Unknown errors go to the DLQ rather than retrying, so a bug cannot hot-loop.
  - Transient (S3 connect/read timeouts, Postgres `OperationalError`): `self.retry` with full-jitter exponential backoff (cap 60 s), up to `PAP_INGEST_MAX_RETRIES` (5), then `failed` + DLQ.
- Idempotency: `ingest_jobs` PK `(tenant_id, idempotency_key)`. Default key = sha256(tenant, bucket, key, etag), so the same object version is ingested once and a re-upload (new etag) is a new job. The producer does not re-publish a seen key; the worker's `claim` is a no-op for a `done` job, so a redelivered message (lost ack) does no work. Chunk ids are deterministic, so even a double run upserts the same rows.
- If publishing fails after the ledger insert, the row is deleted so the caller can retry with the same key.

## Measured

See RESULTS, Phase 1B. Local: 40 docs + 4 poison in ~1.6 s (≈25 docs/s, 4 threads, hash embedder), all 4 poison in the DLQ, 10/10 duplicate enqueues suppressed, redelivery of a done job changed nothing, transient error ×2 → done on attempt 3.

## Rejected

- Redis as broker: no per-message dead-lettering or delivery limits; Redis already serves cache.
- Celery result backend: duplicates the ledger; Postgres is the source of truth.
- Requeue-on-failure for unknown errors: hot loops on bugs.
- Kafka/SQS: more infra than the load needs; SQS is the AWS-runbook alternative (same DLQ semantics via redrive policy).

## Open

- Re-ingesting a document that got shorter leaves its old higher-`ord` chunks; delete-by-doc before upsert is the fix (next phase that touches ingest).
- Tenant comes from a header until auth (Phase 5).
- Only UTF-8 text; PDF/HTML extraction is a later task.
- First local run of the integration test failed once (timeout waiting for jobs, cause not captured); 3 later runs green. CI is the check. UNVERIFIED as non-flaky.

## Pins

rabbitmq 4.2 (`rabbitmq:4.2-management-alpine`), celery 5.7.x, kombu 5.7.x, boto3 1.44.x, moto 5.2.x (tests).
