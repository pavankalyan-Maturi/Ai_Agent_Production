# Load test runbook (P4.4)

Runs on your machine against the compose stack; not in CI. Cost: $0 with `LOAD_MODEL=mock`.

```bash
docker build -t pap-api:dev . && docker build -f deploy/litellm/Dockerfile -t pap-litellm:dev .
# HS256 test tokens: only for a local stack, never a shared one.
export PAP_OIDC_KEY=load-test-only-secret-0123456789abcdef PAP_OIDC_ALGORITHMS='["HS256"]'
# add both to the api service environment (or .env), then:
docker compose -f deploy/compose/docker-compose.yml --env-file .env --profile core up -d --wait
cd loadtest && uvx --from locust==2.47.0 --with pyjwt locust -f locustfile.py --headless \
  -u 50 -r 10 -t 120s -H http://localhost:8000 --only-summary --csv out
```

Record `first-token/*` and `full-stream/*` p50/p95 and the `CACHE ... hit_rate` line in
`docs/RESULTS.md`. Raise `PAP_RATE_LIMIT_RPM` above the per-user rate or you measure 429s.
For real providers set `LOAD_MODEL=agent-fast` and `ANTHROPIC_API_KEY`; at 64 output tokens,
~5k misses cost about $0.2 on Haiku 5.5. Find the knee by stepping `-u` 50/100/200.
