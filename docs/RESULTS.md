# RESULTS

Every number has: commit, environment, command. NOT MEASURED means exactly that.

## Phase 0

| Metric | Value | Env | Command | Commit |
|---|---|---|---|---|
| Unit tests | 11 passed | sandbox, py3.12 | `uv run pytest -q` | 7536ef3 |
| Compose core+obs boot to healthy (images cached) | 16 s | cloud sandbox, 4 vCPU / 15 GB | `compose up --wait` | 7536ef3 |
| Compose core+obs+llmobs boot to healthy, fresh volumes | 25 s | same | `scripts/compose_smoke.sh --llmobs` | 7536ef3 |
| /healthz latency, sequential, n=1000 | p50 1.41 ms, p95 1.79 ms, p99 2.22 ms | same, host -> container | `scripts/bench_probes.py` | 7536ef3 |
| /readyz latency (PG via PgBouncer + Redis), n=1000 | p50 10.67 ms, p95 14.24 ms, p99 16.77 ms | same | same | 7536ef3 |
| /v1/ping (traced) latency, n=1000 | p50 1.68 ms, p95 3.12 ms, p99 8.47 ms | same | same | 7536ef3 |
| Spans: API -> collector -> Langfuse v4 | observations visible via /api/public/v2/observations | same | smoke script | 7536ef3 |
| pip-audit (runtime deps) | no known vulnerabilities | sandbox | `pip-audit -r <uv export>` | 7536ef3 |
| CI (ci, compose-smoke, security) | NOT MEASURED yet: first run on the PR | GitHub Actions | | |
