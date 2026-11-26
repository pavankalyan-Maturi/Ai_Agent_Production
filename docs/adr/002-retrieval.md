# ADR 002: Retrieval stack (Phase 1A)

Status: accepted for Phase 1A, contextual rows pending. Date: 2026-11-26.

## Decision

- Store: one `chunks` table in Postgres 17 + pgvector 0.8.6. PK `(tenant_id, chunk_id)`, HNSW cosine (m=16, ef_construction=64), generated `tsvector` with GIN. Every query filters on `tenant_id`. Chunk ids are sha256 of tenant/doc/ord/text, so re-ingest is an idempotent upsert.
- Tenant filtering: HNSW `iterative_scan=relaxed_order` with `ef_search` and `max_scan_tuples` set per transaction (`set_config(..., true)`, safe through PgBouncer transaction mode), results re-sorted after relaxed order. Without it a small tenant on a shared index gets 0.1 of 10 rows (RESULTS, Phase 1A).
- Lexical: Postgres FTS with `ts_rank_cd`, OR of query lexemes. It is not BM25 and is labelled so everywhere. A BM25 extension (pg_search / VectorChord-bm25) would need a non-stock image; not worth it until FTS is shown to be the bottleneck.
- Fusion: RRF, k=60, 50 candidates from each side.
- Rerank: small CPU cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`), optional per query. On SciFact it adds ~1.3 s p50 on CPU for a gain inside the CI, so it stays off by default and is a per-request option.
- Contextual retrieval (Anthropic method): a short LLM-written context is prepended to each chunk before embedding and FTS. Provider behind one `Contextualizer` protocol: Groq (free tier, OpenAI-compatible HTTP) or Claude Haiku 5.5 (`claude-haiku-5-5`, `cache_control` on the document block; SciFact abstracts are mostly under the 512-token minimum, so expect few cache hits there). Contexts are cached on disk by sha256(prompt version, model, doc, chunk) so each is paid for once.
- Embeddings: `BAAI/bge-small-en-v1.5` via fastembed in CI (CPU). Production embedding goes behind LiteLLM in a later phase.

## Measured

See RESULTS, Phase 1A. Dense 0.925, hybrid 0.939, hybrid+rerank 0.947 recall@20; CIs overlap.

## Groq free tier

The default model `llama-3.1-8b-instant` returned 404 to this account; the code now resolves a model against `/openai/v1/models` (got `openai/gpt-oss-20b`). That run stopped after 23 calls on 429s. The slice needs roughly 4,500 calls (≈3 chunks × 1500 docs, ESTIMATE). Groq free quotas are UNVERIFIED here; 429s with a long retry-after now stop the run cleanly and the cache resumes next time.

## Rejected

- Separate vector DB (Qdrant, etc.): one more stateful service; pgvector with iterative scan meets the filtered-recall need and keeps tenant isolation in SQL.
- BM25 extension now: see above.
- Rerank on by default: latency cost not earned on this data.

## Pins

pgvector 0.8.6 (image `pgvector/pgvector:0.8.6-pg17`), pgvector-python 0.5.1, fastembed 0.9.1 (eval group), anthropic 2.14, httpx2 (Groq client). Prompt version `v2`.
