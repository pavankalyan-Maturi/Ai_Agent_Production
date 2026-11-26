"""Producer side: record the job, then publish. A seen idempotency key is not re-published."""

from __future__ import annotations

from dataclasses import dataclass

from celery import Celery

from apps.ingest.jobs import JobStore, idempotency_key


@dataclass(frozen=True)
class Enqueued:
    ikey: str
    published: bool  # False = duplicate request, existing job kept


def enqueue_ingest(
    app: Celery,
    jobs: JobStore,
    tenant_id: str,
    bucket: str,
    key: str,
    etag: str = "",
    ikey: str | None = None,
) -> Enqueued:
    ikey = ikey or idempotency_key(tenant_id, bucket, key, etag)
    if not jobs.create(tenant_id, ikey, bucket, key):
        return Enqueued(ikey, published=False)
    try:
        app.send_task("pap.ingest_document", args=[tenant_id, bucket, key, ikey])
    except Exception:
        jobs.forget(tenant_id, ikey)  # broker down: let the caller retry with the same key
        raise
    return Enqueued(ikey, published=True)
