"""Locust scenario for /v1/chat/stream (P4.4). Runbook: loadtest/README.md.

Mix: 70% popular prompts (cache-friendly, Zipf over 20), 30% unique prompts (always miss).
Each user is one (tenant, sub) so per-key rate limits apply per user. Tokens are HS256 and
minted locally: only for a stack started with PAP_OIDC_KEY=<secret>, PAP_OIDC_ALGORITHMS.
"""

from __future__ import annotations

import itertools
import os
import random
import time

import jwt
from locust import HttpUser, between, events, task

SECRET = os.environ.get("LOAD_JWT_SECRET", "load-test-only-secret-0123456789abcdef")
ISSUER = os.environ.get("PAP_OIDC_ISSUER", "http://localhost:8080/realms/pap")
MODEL = os.environ.get("LOAD_MODEL", "mock")
_ids = itertools.count()
POPULAR = [f"Summarise the onboarding policy section {i}" for i in range(20)]
WEIGHTS = [1 / (i + 1) for i in range(20)]
CACHE = {"exact": 0, "semantic": 0, "miss": 0}


def _token(tenant: str, sub: str) -> str:
    claims = {
        "iss": ISSUER,
        "aud": "pap-api",
        "sub": sub,
        "tenant_id": tenant,
        "scope": "pap:chat",
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


class ChatUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        n = next(_ids)
        self.headers = {"Authorization": f"Bearer {_token(f't{n % 5}', f'u{n}')}"}

    @task
    def chat(self) -> None:
        r = random.random()  # noqa: S311
        q = random.choices(POPULAR, WEIGHTS)[0] if r < 0.7 else f"unique {time.time_ns()}"  # noqa: S311
        body = {"model": MODEL, "messages": [{"role": "user", "content": q}], "max_tokens": 64}
        t0 = time.perf_counter()
        with self.client.post(
            "/v1/chat/stream",
            json=body,
            headers=self.headers,
            stream=True,
            catch_response=True,
            name="chat/stream",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
                return
            first, kind = None, "miss"
            for line in resp.iter_lines():
                if line.startswith(b"data:") and b'"cache"' in line:
                    kind = next((k for k in CACHE if f'"cache": "{k}"'.encode() in line), kind)
                elif line.startswith(b"data:") and b'"text"' in line and first is None:
                    first = time.perf_counter()
            CACHE[kind] += 1
            end = time.perf_counter()
            for name, t in ((f"first-token/{kind}", first), (f"full-stream/{kind}", end)):
                if t is not None:
                    events.request.fire(
                        request_type="SSE",
                        name=name,
                        response_time=(t - t0) * 1000,
                        response_length=0,
                        exception=None,
                        context={},
                    )
            resp.success()


@events.quitting.add_listener
def _report(environment, **_kw) -> None:  # type: ignore[no-untyped-def]
    total = sum(CACHE.values()) or 1
    print(
        f"CACHE exact={CACHE['exact']} semantic={CACHE['semantic']} miss={CACHE['miss']} "
        f"hit_rate={(CACHE['exact'] + CACHE['semantic']) / total:.1%}"
    )
