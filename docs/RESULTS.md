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

Run: [eval-retrieval #8](https://github.com/pavankalyan-Maturi/Ai_Agent_Production/actions/runs/45890109396), commit 579a163, `uv run python -m evals.retrieval.run --queries 200 --corpus-size 1500`.

| Variant | Retrieval | Recall@20 [95% CI] | Failure@20 | nDCG@10 [95% CI] | p50 / p95 ms |
|---|---|---|---|---|---|
| naive | dense | 0.925 [0.890, 0.958] | 0.075 | 0.796 [0.748, 0.837] | 36 / 43 |
| naive | hybrid (RRF k=60) | 0.939 [0.906, 0.969] | 0.061 | 0.776 [0.729, 0.819] | 66 / 107 |
| naive | hybrid + rerank (50 → 20) | 0.947 [0.917, 0.974] | 0.053 | 0.808 [0.764, 0.848] | 1401 / 2602 |
| contextual | all three | NOT MEASURED: Groq free tier hit its quota after 23 calls (see ADR 002) | | | |

Reading: the CIs overlap; on this slice hybrid and rerank are not significantly better than dense. Rerank costs ~20x latency on CPU. Hybrid lowers nDCG@10 slightly while raising recall@20.

Filtered ANN (tenant filter on a shared HNSW index), `tests/retrieval/test_store_integration.py`, local sandbox, pgvector 0.8.6, 20k rows big tenant + 200 rows small tenant, k=10:

| Setting | Recall@10 (small tenant) | Mean rows returned |
|---|---|---|
| `hnsw.iterative_scan=off` | 0.015 | 0.1 of 10 |
| `hnsw.iterative_scan=relaxed_order`, ef_search 100, max_scan_tuples 20000 | 0.845 | 10 of 10 |

Commit 7364b2c, `PAP_TEST_DATABASE_URL=... uv run pytest -m integration -s`. CI asserts on ≥ 0.75, off < 0.2.
