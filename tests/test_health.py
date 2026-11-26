from tests.conftest import _down, _ok


def test_healthz_is_dependency_free(make_client) -> None:
    with make_client({"postgres": _down}) as c:
        r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_ok(client) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert set(body["checks"]) == {"postgres", "redis"}


def test_readyz_503_when_dependency_down(make_client) -> None:
    with make_client({"postgres": _down, "redis": _ok}) as c:
        r = c.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert body["checks"]["postgres"]["ok"] is False
    assert body["checks"]["redis"]["ok"] is True


def test_readyz_error_never_leaks_dsn(make_client) -> None:
    with make_client({"postgres": _down}) as c:
        text = c.get("/readyz").text
    assert "secret" not in text
    assert "postgresql://" not in text


def test_readyz_times_out_slow_dependency(make_client) -> None:
    import asyncio

    async def slow(_):
        await asyncio.sleep(5)

    with make_client({"slow": slow}) as c:
        r = c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["slow"]["error"] == "TimeoutError"
