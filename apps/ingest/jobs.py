"""Idempotency ledger for ingest jobs, keyed by (tenant_id, idempotency_key)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

import psycopg

Status = Literal["queued", "running", "done", "failed"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_jobs (
  tenant_id       text NOT NULL,
  idempotency_key text NOT NULL,
  bucket          text NOT NULL,
  object_key      text NOT NULL,
  status          text NOT NULL CHECK (status IN ('queued','running','done','failed')),
  attempts        int  NOT NULL DEFAULT 0,
  chunks          int,
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, idempotency_key)
);
"""


def idempotency_key(tenant_id: str, bucket: str, key: str, etag: str = "") -> str:
    """Same object version -> same key. Pass the S3 ETag so a re-upload is a new job."""
    raw = f"{tenant_id}\x00{bucket}\x00{key}\x00{etag}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Job:
    tenant_id: str
    idempotency_key: str
    bucket: str
    object_key: str
    status: Status
    attempts: int
    chunks: int | None
    error: str | None


class JobStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _conn(self) -> psycopg.Connection:
        # prepare_threshold=None: safe behind PgBouncer transaction pooling.
        return psycopg.connect(self.dsn, prepare_threshold=None, autocommit=True)

    def migrate(self) -> None:
        # Serialised across processes; see PgStore.migrate.
        with psycopg.connect(self.dsn, prepare_threshold=None) as c, c.transaction():
            c.execute("SELECT pg_advisory_xact_lock(hashtext('pap.migrate.ingest_jobs'))")
            c.execute(SCHEMA)

    def create(self, tenant_id: str, ikey: str, bucket: str, object_key: str) -> bool:
        """True if this is a new job; False if the key was seen before (do not enqueue)."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO ingest_jobs (tenant_id, idempotency_key, bucket, object_key, status)"
                " VALUES (%s, %s, %s, %s, 'queued') ON CONFLICT DO NOTHING RETURNING 1",
                (tenant_id, ikey, bucket, object_key),
            ).fetchone()
        return row is not None

    def claim(self, tenant_id: str, ikey: str) -> bool:
        """Mark running unless already done. Redelivery after a crash re-claims 'running'."""
        with self._conn() as c:
            row = c.execute(
                "UPDATE ingest_jobs SET status='running', attempts=attempts+1, updated_at=now()"
                " WHERE tenant_id=%s AND idempotency_key=%s AND status <> 'done' RETURNING 1",
                (tenant_id, ikey),
            ).fetchone()
        return row is not None

    def forget(self, tenant_id: str, ikey: str) -> None:
        with self._conn() as c:
            c.execute(
                "DELETE FROM ingest_jobs WHERE tenant_id=%s AND idempotency_key=%s"
                " AND status='queued'",
                (tenant_id, ikey),
            )

    def finish(self, tenant_id: str, ikey: str, chunks: int) -> None:
        self._set(tenant_id, ikey, "done", chunks=chunks)

    def fail(self, tenant_id: str, ikey: str, error: str) -> None:
        self._set(tenant_id, ikey, "failed", error=error[:500])

    def _set(
        self,
        tenant_id: str,
        ikey: str,
        status: Status,
        *,
        chunks: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE ingest_jobs SET status=%s, chunks=COALESCE(%s, chunks), error=%s,"
                " updated_at=now() WHERE tenant_id=%s AND idempotency_key=%s",
                (status, chunks, error, tenant_id, ikey),
            )

    def get(self, tenant_id: str, ikey: str) -> Job | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT tenant_id, idempotency_key, bucket, object_key, status, attempts, chunks,"
                " error FROM ingest_jobs WHERE tenant_id=%s AND idempotency_key=%s",
                (tenant_id, ikey),
            ).fetchone()
        return Job(*row) if row else None
