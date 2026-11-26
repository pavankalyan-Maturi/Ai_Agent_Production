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

## Phase 1A: retrieval

SciFact slice: BEIR SciFact test (files from the `mteb/scifact` Hugging Face mirror), first 200 queries by id, 1500 docs = all relevant docs + seeded (13) distractors. Chunks 96 words / 16 overlap. Embedder `BAAI/bge-small-en-v1.5` (384-d), reranker `Xenova/ms-marco-MiniLM-L-6-v2`, both CPU via fastembed. FTS is Postgres `ts_rank_cd`, not BM25. Doc-level metrics, bootstrap 95% CIs. Latency is end-to-end per query on a GitHub-hosted runner (shared CPU, noisy).

Run: [eval-retrieval #12, attempt 2](https://github.com/pavankalyan-Maturi/Ai_Agent_Production/actions/runs/45894089753), commit 7b13dc9, `uv run python -m evals.retrieval.run --queries 200 --corpus-size 1500` (naive rows identical to run #8 at 579a163). Contextual headers: `claude-haiku-5-5`, effort low, thinking off, prompt v2.

| Variant | Retrieval | Recall@20 [95% CI] | Failure@20 | nDCG@10 [95% CI] | p50 / p95 ms |
|---|---|---|---|---|---|
| naive | dense | 0.925 [0.890, 0.958] | 0.075 | 0.796 [0.748, 0.837] | 54 / 64 |
| naive | hybrid (RRF k=60) | 0.939 [0.906, 0.969] | 0.061 | 0.776 [0.729, 0.819] | 84 / 122 |
| naive | hybrid + rerank (50 → 20) | 0.947 [0.917, 0.974] | 0.053 | 0.808 [0.764, 0.848] | 1767 / 2825 |
| contextual | dense | 0.905 [0.864, 0.943] | 0.095 | 0.797 [0.748, 0.839] | 31 / 45 |
| contextual | hybrid | 0.927 [0.892, 0.959] | 0.073 | 0.792 [0.746, 0.836] | 81 / 124 |
| contextual | hybrid + rerank | 0.942 [0.910, 0.972] | 0.058 | 0.805 [0.761, 0.846] | 1729 / 2790 |

Contextual generation: 4,522 Haiku calls, 2.11M uncached input, 0.44M cache-write, 1.49M cache-read, 0.53M output tokens; est. $0.67 (ESTIMATE, from usage at list price; actual billing not read).

Reading: every CI overlaps. Contextual headers did not help on SciFact: recall@20 is 0.012-0.020 lower in each rung, nDCG@10 flat (not significant either way). Likely cause (UNVERIFIED): SciFact docs are single short abstracts with the title already prepended to every chunk, so the extra context adds little and dilutes the 384-d embedding. Hybrid and rerank are not significantly better than dense; rerank costs ~20-30x latency on CPU.

Filtered ANN (tenant filter on a shared HNSW index), `tests/retrieval/test_store_integration.py`, local sandbox, pgvector 0.8.6, 20k rows big tenant + 200 rows small tenant, k=10:

| Setting | Recall@10 (small tenant) | Mean rows returned |
|---|---|---|
| `hnsw.iterative_scan=off` | 0.015 | 0.1 of 10 |
| `hnsw.iterative_scan=relaxed_order`, ef_search 100, max_scan_tuples 20000 | 0.845 | 10 of 10 |

Commit 7364b2c, `PAP_TEST_DATABASE_URL=... uv run pytest -m integration -s`. CI asserts on ≥ 0.75, off < 0.2.

## Phase 1B: ingestion

`tests/ingest/test_ingest_integration.py`, local sandbox (4 vCPU), RabbitMQ 4.2 + pgvector 0.8.6 containers, S3 = moto server, in-process Celery worker (threads, concurrency 4), hash embedder (so this measures the pipeline, not embedding). Command: `PAP_TEST_DATABASE_URL=... PAP_TEST_BROKER_URL=... uv run pytest -m integration tests/ingest -s`.

| Metric | Value |
|---|---|
| 40 docs (300 words each) + 4 poison, enqueue → all jobs terminal | 1.56-1.64 s (≈25 docs/s), 160 chunks |
| Poison docs in `ingest.dlq` | 4 / 4 (binary, empty, oversize, missing) |
| Duplicate enqueues suppressed | 10 / 10 |
| Redelivered message for a done job | 0 new chunks, attempts stays 1 |
| Transient S3 error ×2 | done on attempt 3 |

First run on a fresh database lost one message: a migration race (fixed, ADR 003; repro 5/5 trials with UniqueViolation before, 0 after; regression test `test_concurrent_migrate_is_safe`). CI ([integration, 251a7b5](https://github.com/pavankalyan-Maturi/Ai_Agent_Production/actions/runs/45902058992/job/140189945175), fresh DB + broker): 40 docs + 4 poison in 0.98 s (41 docs/s), DLQ 4/4, dedupe 10/10, retry ×2 → done on attempt 3; same run's filtered-ANN test recall@10 off 0.020 → on 0.830. Throughput with the real fastembed model: NOT MEASURED.
