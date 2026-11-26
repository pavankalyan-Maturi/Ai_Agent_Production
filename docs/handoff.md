# Handoff

## Done
- Phase 0 merged (PR #1).
- Phase 1A (PR #2, draft, not merged): retrieval core in `retrieval/` (chunker, embedder, PgStore with HNSW + FTS + iterative scan, RRF, cross-encoder rerank, contextual headers via Groq or Claude Haiku 5.5 with disk cache), SciFact ablation in `evals/retrieval/`, CI workflow `eval-retrieval.yml`. ADR 002. Numbers in RESULTS, Phase 1A.

## Verified
- Gate locally: ruff, format, mypy strict, 28 unit tests; integration tests against local pgvector (filtered recall off 0.015 → on 0.845).
- CI eval run #8: naive dense / hybrid / hybrid+rerank measured on 200 queries, 1500 docs.

## Not verified
- Contextual rows: NOT MEASURED. Groq free tier 404'd on llama-3.1-8b-instant, then hit 429s after 23 calls on openai/gpt-oss-20b. Quota handling (stop + resume from cache) is unit-tested only until the next CI run.
- Anthropic path never called a real API here (no key).
- This sandbox cannot reach Hugging Face, Groq or Anthropic; CI is the only verifier for downloads and providers.

## Open
- Decision pending with the user: Haiku (~$0.50, ESTIMATE) vs Groq over several days vs smaller slice vs skip, for contextual rows.
- PR B (P1.1): S3 → RabbitMQ → Celery ingest with retries, DLQ, idempotency key. Not started.

## Next step
Get the contextual decision, fill the contextual rows, mark PR #2 ready. Then PR B on a new branch after PR #2 merges.

## Versions pinned
docs/SPEC.md Pins table, plus ADR 002 (pgvector 0.8.6, pgvector-python 0.5.1, fastembed 0.9.1, anthropic 2.14, prompt v2).
