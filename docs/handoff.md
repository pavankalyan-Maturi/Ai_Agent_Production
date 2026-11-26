# Handoff

## Done
- Phase 0 merged (PR #1).
- Phase 1A (PR #2, draft, not merged): retrieval core in `retrieval/` (chunker, embedder, PgStore with HNSW + FTS + iterative scan, RRF, cross-encoder rerank, contextual headers via Groq or Claude Haiku 5.5 with disk cache), SciFact ablation in `evals/retrieval/`, CI workflow `eval-retrieval.yml`. ADR 002. Numbers in RESULTS, Phase 1A.

## Verified
- Gate locally: ruff, format, mypy strict, 28 unit tests; integration tests against local pgvector (filtered recall off 0.015 → on 0.845).
- CI eval run #12: all six ablation rows measured on 200 queries, 1500 docs. Contextual (Haiku 5.5) gave no gain on SciFact (RESULTS, ADR 002).

## Not verified
- Groq path never completed a full run (free tier 8k TPM); its quota handling is unit-tested only.
- Haiku actual billing not read; $0.67 is computed from usage.
- This sandbox cannot reach Hugging Face, Groq or Anthropic; CI is the only verifier for downloads and providers.

## Open
- PR B (P1.1): S3 → RabbitMQ → Celery ingest with retries, DLQ, idempotency key. Not started.

## Next step
Review and merge PR #2. Then PR B (P1.1 ingest) on the same branch, restarted from main.

## Versions pinned
docs/SPEC.md Pins table, plus ADR 002 (pgvector 0.8.6, pgvector-python 0.5.1, fastembed 0.9.1, anthropic 2.14, prompt v2).
