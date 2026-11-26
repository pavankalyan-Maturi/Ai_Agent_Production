import botocore.exceptions
import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.settings import Settings
from apps.api.telemetry import build_tracer_provider
from apps.ingest.enqueue import enqueue_ingest
from apps.ingest.jobs import idempotency_key
from apps.ingest.tasks import PoisonDocument, backoff, fetch_text


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def head_object(self, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
            )
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket: str, Key: str) -> dict:
        return {"Body": FakeBody(self.objects[Key])}


class FakeJobs:
    def __init__(self) -> None:
        self.rows: set[tuple[str, str]] = set()

    def create(self, tenant_id: str, ikey: str, bucket: str, key: str) -> bool:
        if (tenant_id, ikey) in self.rows:
            return False
        self.rows.add((tenant_id, ikey))
        return True

    def forget(self, tenant_id: str, ikey: str) -> None:
        self.rows.discard((tenant_id, ikey))


class FakeCelery:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[list[str]] = []
        self.fail = fail

    def send_task(self, name: str, args: list[str]) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append(args)


def test_idempotency_key_is_stable_and_version_sensitive() -> None:
    a = idempotency_key("t1", "b", "k", "etag1")
    assert a == idempotency_key("t1", "b", "k", "etag1")
    assert a != idempotency_key("t1", "b", "k", "etag2")
    assert a != idempotency_key("t2", "b", "k", "etag1")  # tenant is part of the key


@pytest.mark.parametrize(
    ("objects", "key", "match"),
    [
        ({}, "missing.txt", "missing object"),
        ({"big.txt": b"x" * 11}, "big.txt", "too large"),
        ({"bin.txt": b"\xff\xfe\x00"}, "bin.txt", "UTF-8"),
        ({"empty.txt": b"  \n"}, "empty.txt", "empty"),
    ],
)
def test_fetch_text_poison(objects: dict, key: str, match: str) -> None:
    with pytest.raises(PoisonDocument, match=match):
        fetch_text(FakeS3(objects), "b", key, max_bytes=10)


def test_fetch_text_ok_and_backoff_bounds() -> None:
    assert fetch_text(FakeS3({"a.txt": b"hello"}), "b", "a.txt", 10) == "hello"
    assert all(0 <= backoff(r) <= 60 for r in range(12))


def test_enqueue_dedupes_and_forgets_on_broker_failure() -> None:
    jobs, app = FakeJobs(), FakeCelery()
    first = enqueue_ingest(app, jobs, "t1", "b", "k", "e")  # type: ignore[arg-type]
    again = enqueue_ingest(app, jobs, "t1", "b", "k", "e")  # type: ignore[arg-type]
    assert first.published and not again.published and len(app.sent) == 1

    down = FakeCelery(fail=True)
    with pytest.raises(ConnectionError):
        enqueue_ingest(down, jobs, "t1", "b", "k2")  # type: ignore[arg-type]
    assert enqueue_ingest(app, jobs, "t1", "b", "k2").published  # type: ignore[arg-type]


def test_ingest_endpoint() -> None:
    jobs, celery = FakeJobs(), FakeCelery()
    s = Settings(env="ci")
    app = create_app(s, build_tracer_provider(s), {}, ingest_deps=lambda: (celery, jobs))
    with TestClient(app) as c:
        body = {"bucket": "docs", "key": "a/b.txt", "etag": "e1"}
        r1 = c.post("/v1/ingest", json=body, headers={"X-Tenant-Id": "acme"})
        r2 = c.post("/v1/ingest", json=body, headers={"X-Tenant-Id": "acme"})
        bad = c.post("/v1/ingest", json=body, headers={"X-Tenant-Id": "Robert'); DROP"})
        missing = c.post("/v1/ingest", json=body)
    assert r1.status_code == 202 and r1.json()["published"] is True
    assert r2.json() == {"idempotency_key": r1.json()["idempotency_key"], "published": False}
    assert bad.status_code == 422 and missing.status_code == 422
    assert celery.sent == [["acme", "docs", "a/b.txt", r1.json()["idempotency_key"]]]
