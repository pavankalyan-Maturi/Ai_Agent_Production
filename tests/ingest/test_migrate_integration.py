"""Concurrent first-start migrations must not race (CI hit UniqueViolation on pg_type)."""

import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from apps.ingest.jobs import JobStore
from retrieval.store import PgStore

DSN = os.environ.get("PAP_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="PAP_TEST_DATABASE_URL not set"),
]


def test_concurrent_migrate_is_safe() -> None:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS ingest_jobs")
        c.execute("DROP TABLE IF EXISTS chunks")

    def go(i: int) -> None:
        JobStore(DSN).migrate() if i % 2 else PgStore(DSN, 384).migrate()

    with ThreadPoolExecutor(8) as ex:
        for f in [ex.submit(go, i) for i in range(16)]:
            f.result()  # raises UniqueViolation without the advisory lock
