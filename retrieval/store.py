"""pgvector + Postgres FTS chunk store. Every read and write is scoped by tenant_id.

Works through PgBouncer (transaction pooling): no server-side prepared statements,
session settings only via SET LOCAL / set_config(..., is_local => true) inside a transaction.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from retrieval.embedding import Vec
from retrieval.types import Chunk, Hit

IterativeScan = Literal["off", "relaxed_order", "strict_order"]

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    tenant_id  text        NOT NULL,
    chunk_id   text        NOT NULL,
    doc_id     text        NOT NULL,
    ord        int         NOT NULL,
    content    text        NOT NULL,
    context    text        NOT NULL DEFAULT '',
    embedding  vector({dim}) NOT NULL,
    tsv        tsvector GENERATED ALWAYS AS
               (to_tsvector('english', context || ' ' || content)) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_tenant_doc ON chunks (tenant_id, doc_id);
"""


class PgStore:
    def __init__(self, dsn: str, dim: int) -> None:
        self.dsn = dsn
        self.dim = dim

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.dsn, prepare_threshold=None) as conn:
            register_vector(conn)
            yield conn

    def migrate(self) -> None:
        # One transaction + advisory xact lock: concurrent workers racing on CREATE ... IF NOT
        # EXISTS otherwise hit unique violations on pg_type. Safe behind PgBouncer (xact-scoped).
        with psycopg.connect(self.dsn, prepare_threshold=None) as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('pap.migrate.chunks'))")
            conn.execute(SCHEMA.format(dim=int(self.dim)))  # dim is an int, not user input

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Vec]) -> int:
        """Idempotent: the same (tenant_id, chunk_id) overwrites, so redeliveries are harmless."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors differ in length")
        rows = [
            (c.tenant_id, c.chunk_id, c.doc_id, c.ord, c.content, c.context, np.asarray(v))
            for c, v in zip(chunks, vectors, strict=True)
        ]
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (tenant_id, chunk_id, doc_id, ord, content, context, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, chunk_id) DO UPDATE
                SET content = EXCLUDED.content, context = EXCLUDED.context,
                    embedding = EXCLUDED.embedding
                """,
                rows,
            )
        return len(rows)

    def count(self, tenant_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count(*) FROM chunks WHERE tenant_id = %s", (tenant_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def dense(
        self,
        tenant_id: str,
        query_vec: Vec,
        k: int,
        *,
        ef_search: int = 40,
        iterative_scan: IterativeScan = "relaxed_order",
        max_scan_tuples: int = 20_000,
        exact: bool = False,
    ) -> list[Hit]:
        """ANN search filtered to one tenant.

        A selective WHERE on an HNSW scan returns fewer than k rows once the first ef_search
        candidates are filtered out. pgvector >= 0.8 iterative scans keep walking the graph.
        relaxed_order can emit slightly out-of-order rows, so results are re-sorted here.
        `exact=True` disables the index (ground truth for recall tests).
        """
        q = np.asarray(query_vec)
        with self._conn() as conn, conn.transaction():
            if exact:
                conn.execute("SELECT set_config('enable_indexscan', 'off', true)")
            else:
                conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef_search),))
                conn.execute(
                    "SELECT set_config('hnsw.iterative_scan', %s, true)", (iterative_scan,)
                )
                conn.execute(
                    "SELECT set_config('hnsw.max_scan_tuples', %s, true)", (str(max_scan_tuples),)
                )
            rows = conn.execute(
                """
                SELECT chunk_id, doc_id, content, embedding <=> %s AS dist
                FROM chunks WHERE tenant_id = %s
                ORDER BY embedding <=> %s LIMIT %s
                """,
                (q, tenant_id, q, k),
            ).fetchall()
        rows.sort(key=lambda r: r[3])
        return [Hit(r[0], r[1], 1.0 - float(r[3]), r[2]) for r in rows]

    def fts(self, tenant_id: str, query: str, k: int) -> list[Hit]:
        """Postgres full-text search, OR over query lexemes, ranked by ts_rank_cd.

        Not BM25 (no IDF saturation); labelled "FTS" in results, not "BM25".
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                WITH q AS (
                  SELECT replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery AS q
                )
                SELECT chunk_id, doc_id, content, ts_rank_cd(tsv, q.q) AS r
                FROM chunks, q
                WHERE tenant_id = %s AND tsv @@ q.q
                ORDER BY r DESC, chunk_id LIMIT %s
                """,
                (query, tenant_id, k),
            ).fetchall()
        return [Hit(r[0], r[1], float(r[3]), r[2]) for r in rows]

    def fetch_doc(self, tenant_id: str, doc_id: str) -> str | None:
        """Reassemble one document from its chunks, in order. Tenant-scoped."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT content FROM chunks WHERE tenant_id = %s AND doc_id = %s ORDER BY ord",
                (tenant_id, doc_id),
            ).fetchall()
        return "\n".join(r[0] for r in rows) if rows else None

    def delete_tenant(self, tenant_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks WHERE tenant_id = %s", (tenant_id,))
