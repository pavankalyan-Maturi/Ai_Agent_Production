"""Real RabbitMQ + Postgres/pgvector + S3 (moto server) + an in-process Celery worker.

Needs PAP_TEST_DATABASE_URL and PAP_TEST_BROKER_URL (CI job `integration` sets both).
Run with -s to see the measured numbers.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import boto3
import psycopg
import pytest

DSN = os.environ.get("PAP_TEST_DATABASE_URL")
BROKER = os.environ.get("PAP_TEST_BROKER_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (DSN and BROKER), reason="PAP_TEST_DATABASE_URL/BROKER_URL not set"),
]
TENANT = "it-ingest"
BUCKET = "docs"
N_GOOD = 40


@pytest.fixture(scope="module")
def env() -> Iterator[dict]:
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    os.environ.update(
        PAP_DATABASE_URL=DSN or "",
        PAP_BROKER_URL=BROKER or "",
        PAP_S3_ENDPOINT_URL=endpoint,
        PAP_S3_ACCESS_KEY_ID="test",
        PAP_S3_SECRET_ACCESS_KEY="test",  # noqa: S106 - moto dummy credentials
        PAP_EMBEDDER="hash",
        PAP_INGEST_MAX_BYTES="20000",
    )
    from apps.api.settings import get_settings
    from apps.ingest import tasks

    get_settings.cache_clear()
    tasks._build_deps.cache_clear()
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",  # noqa: S106
    )
    s3.create_bucket(Bucket=BUCKET)
    with psycopg.connect(DSN or "", autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS ingest_jobs")
        c.execute("DROP TABLE IF EXISTS chunks")
    yield {"s3": s3, "endpoint": endpoint}
    server.stop()


@pytest.fixture(scope="module")
def worker(env: dict) -> Iterator[object]:
    from celery.contrib.testing.worker import start_worker

    from apps.api.settings import get_settings
    from apps.ingest.celery_app import declare_topology, make_app

    app = make_app(get_settings())
    declare_topology(app)
    with app.connection_for_write() as conn:
        for q in ("ingest", "ingest.dlq"):
            conn.default_channel.queue_purge(q)
    with start_worker(
        app,
        pool="threads",
        concurrency=4,
        perform_ping_check=False,
        queues=["ingest"],
        shutdown_timeout=30,
    ):
        yield app


def _queue_depth(app: object, name: str) -> int:
    with app.connection_for_write() as conn:  # type: ignore[attr-defined]
        return int(conn.default_channel.queue_declare(name, passive=True).message_count)


def _wait(jobs: object, keys: list[str], timeout: float = 120) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while True:
        st = {k: jobs.get(TENANT, k).status for k in keys}  # type: ignore[attr-defined]
        if all(v in {"done", "failed"} for v in st.values()) or time.monotonic() > deadline:
            return st
        time.sleep(0.2)


def test_ingest_dlq_and_idempotency(env: dict, worker: object) -> None:
    from apps.ingest.enqueue import enqueue_ingest
    from apps.ingest.jobs import JobStore
    from retrieval.store import PgStore

    s3 = env["s3"]
    jobs = JobStore(DSN or "")
    jobs.migrate()
    words = "vitamin fracture statin cholesterol graph molecule protein cell".split()
    for i in range(N_GOOD):
        body = " ".join(words[(i + j) % len(words)] for j in range(300))
        s3.put_object(Bucket=BUCKET, Key=f"good/{i}.txt", Body=body.encode())
    s3.put_object(Bucket=BUCKET, Key="poison/binary.bin", Body=b"\xff\xfe\x00\x81" * 10)
    s3.put_object(Bucket=BUCKET, Key="poison/empty.txt", Body=b"   ")
    s3.put_object(Bucket=BUCKET, Key="poison/huge.txt", Body=b"a " * 20000)
    poison = ["poison/binary.bin", "poison/empty.txt", "poison/huge.txt", "poison/missing.txt"]

    t0 = time.perf_counter()
    good = [enqueue_ingest(worker, jobs, TENANT, BUCKET, f"good/{i}.txt") for i in range(N_GOOD)]
    dupes = [enqueue_ingest(worker, jobs, TENANT, BUCKET, f"good/{i}.txt") for i in range(10)]
    bad = [enqueue_ingest(worker, jobs, TENANT, BUCKET, k) for k in poison]
    st = _wait(jobs, [e.ikey for e in good + bad])
    elapsed = time.perf_counter() - t0

    assert all(e.published for e in good + bad) and not any(e.published for e in dupes)
    assert [st[e.ikey] for e in good] == ["done"] * N_GOOD, st
    assert [st[e.ikey] for e in bad] == ["failed"] * len(poison), st
    time.sleep(1)  # dead-lettering is async
    dlq = _queue_depth(worker, "ingest.dlq")
    assert dlq == len(poison)

    store = PgStore(DSN or "", 384)
    before = store.count(TENANT)
    # Redelivery of an already-done job (e.g. ack lost): the ledger makes it a no-op.
    worker.send_task(  # type: ignore[attr-defined]
        "pap.ingest_document", args=[TENANT, BUCKET, "good/0.txt", good[0].ikey]
    )
    time.sleep(2)
    assert store.count(TENANT) == before
    assert jobs.get(TENANT, good[0].ikey).attempts == 1  # type: ignore[union-attr]
    assert store.count("other-tenant") == 0
    print(
        f"\nINGEST: {N_GOOD} docs + {len(poison)} poison in {elapsed:.2f}s "
        f"({N_GOOD / elapsed:.1f} docs/s, 4 threads, hash embedder), {before} chunks, "
        f"dlq={dlq}, duplicate enqueues suppressed={sum(not e.published for e in dupes)}/10"
    )


def test_transient_error_retries_then_succeeds(
    env: dict, worker: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    import botocore.exceptions

    from apps.ingest import tasks
    from apps.ingest.enqueue import enqueue_ingest
    from apps.ingest.jobs import JobStore

    env["s3"].put_object(Bucket=BUCKET, Key="flaky.txt", Body=b"flaky but fine " * 20)
    real, calls = tasks.fetch_text, {"n": 0}

    def flaky(*a: object, **kw: object) -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise botocore.exceptions.EndpointConnectionError(endpoint_url="http://s3")
        return real(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(tasks, "fetch_text", flaky)
    monkeypatch.setattr(tasks, "backoff", lambda r: 0.2)
    jobs = JobStore(DSN or "")
    e = enqueue_ingest(worker, jobs, TENANT, BUCKET, "flaky.txt")
    st = _wait(jobs, [e.ikey], timeout=60)
    job = jobs.get(TENANT, e.ikey)
    assert st[e.ikey] == "done" and job is not None and job.attempts == 3
    print(f"\nRETRY: transient S3 error x2 -> done on attempt {job.attempts}")
