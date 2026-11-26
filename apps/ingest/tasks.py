"""The ingest task: fetch from S3, chunk, embed, upsert. Poison -> DLQ, transient -> retry."""

from __future__ import annotations

import logging
import random
from functools import lru_cache
from typing import Any

import botocore.exceptions
import psycopg
from celery import Task, shared_task
from celery.exceptions import Reject

from apps.api.settings import Settings, get_settings
from apps.ingest.jobs import JobStore
from retrieval.embedding import Embedder, FastEmbedEmbedder, HashEmbedder
from retrieval.pipeline import index_documents
from retrieval.store import PgStore
from retrieval.types import Document

log = logging.getLogger(__name__)


class PoisonDocument(Exception):
    """Retrying cannot help: bad encoding, empty, too large, missing object."""


TRANSIENT: tuple[type[BaseException], ...] = (
    botocore.exceptions.EndpointConnectionError,
    botocore.exceptions.ConnectTimeoutError,
    botocore.exceptions.ReadTimeoutError,
    psycopg.OperationalError,
)


def backoff(retries: int, base: float = 2.0, cap: float = 60.0) -> float:
    """Full-jitter exponential backoff (seconds)."""
    return random.uniform(0, min(cap, base * 2**retries))  # noqa: S311


@lru_cache(maxsize=1)
def _deps() -> tuple[Settings, Any, JobStore, PgStore, Embedder]:
    import boto3

    s = get_settings()
    s3 = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        region_name=s.s3_region,
        aws_access_key_id=s.s3_access_key_id.get_secret_value() if s.s3_access_key_id else None,
        aws_secret_access_key=(
            s.s3_secret_access_key.get_secret_value() if s.s3_secret_access_key else None
        ),
    )
    embedder: Embedder = (
        HashEmbedder(s.embedding_dim) if s.embedder == "hash" else FastEmbedEmbedder()
    )
    dsn = str(s.database_url)
    jobs, store = JobStore(dsn), PgStore(dsn, embedder.dim)
    jobs.migrate()
    store.migrate()
    return s, s3, jobs, store, embedder


def fetch_text(s3: Any, bucket: str, key: str, max_bytes: int) -> str:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except botocore.exceptions.ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise PoisonDocument(f"missing object s3://{bucket}/{key}") from e
        raise
    if int(head["ContentLength"]) > max_bytes:
        raise PoisonDocument(f"object too large: {head['ContentLength']} > {max_bytes} bytes")
    body: bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PoisonDocument("not UTF-8 text") from e
    if not text.strip():
        raise PoisonDocument("empty document")
    return text


@shared_task(bind=True, name="pap.ingest_document", acks_late=True, max_retries=None)
def ingest_document(
    self: Task, tenant_id: str, bucket: str, key: str, ikey: str
) -> dict[str, object]:
    s, s3, jobs, store, embedder = _deps()
    if not jobs.claim(tenant_id, ikey):
        return {"status": "duplicate", "ikey": ikey}
    try:
        text = fetch_text(s3, bucket, key, s.ingest_max_bytes)
        doc = Document(
            tenant_id=tenant_id,
            doc_id=f"s3://{bucket}/{key}",
            text=text,
            title=key.rsplit("/", 1)[-1],
        )
        n = index_documents(store, embedder, [doc])
    except PoisonDocument as e:
        jobs.fail(tenant_id, ikey, f"poison: {e}")
        log.warning("ingest poison tenant=%s ikey=%s: %s", tenant_id, ikey, e)
        raise Reject(str(e), requeue=False) from e  # -> dead-letter exchange -> ingest.dlq
    except TRANSIENT as e:
        if self.request.retries >= s.ingest_max_retries:
            jobs.fail(tenant_id, ikey, f"retries exhausted: {type(e).__name__}: {e}")
            raise Reject(f"retries exhausted: {e}", requeue=False) from e
        raise self.retry(exc=e, countdown=backoff(self.request.retries)) from e
    except Exception as e:  # unknown bug: park in the DLQ for inspection, don't hot-loop
        jobs.fail(tenant_id, ikey, f"error: {type(e).__name__}: {e}")
        log.exception("ingest failed tenant=%s ikey=%s", tenant_id, ikey)
        raise Reject(f"{type(e).__name__}: {e}", requeue=False) from e
    jobs.finish(tenant_id, ikey, n)
    return {"status": "done", "ikey": ikey, "chunks": n}
