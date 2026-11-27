# COSTS (Claude usage, ESTIMATES unless marked actual)

Claude cannot read your balance. Paste actuals from Settings > Usage into the last column.

| Date | Phase | Thread purpose | Model | Rough tokens (ESTIMATE) | Est. USD | Actual USD |
|---|---|---|---|---|---|---|
| 2026-11-26 | P0 | Read reference PDF, scaffold, compose, CI, docs, PR | Opus-class (session default) | ~1.5M in (mostly cached), ~60k out | ~$5-8 | |
| 2026-11-26 | P1A | Retrieval core, SciFact ablation, CI fixes (dataset mirror, Groq model/quota) | Opus-class (session default) | ~3M in (mostly cached), ~90k out | ~$8-12 | |
| 2026-11-26 | P1A | Anthropic API (CI eval, not Claude Code): 4,522 Haiku 5.5 calls for contextual headers | claude-haiku-5-5 | 2.1M in + 1.9M cache, 0.53M out (measured usage) | ~$0.67 | |
| 2026-11-26 | P1B | Ingest pipeline (Celery/RabbitMQ/S3, DLQ, idempotency), tests, ADR 003, PR | Opus-class (session default) | ~1.5M in (mostly cached), ~40k out | ~$4-6 | |
| 2026-11-26 | P2A | Agent graph, Postgres checkpointer + kill/resume test, budgets, HITL, ADR 004, PR | Opus-class (session default) | ~1.2M in (mostly cached), ~35k out | ~$3-5 | |
| 2026-11-27 | P2B | Compaction, experience memory + gate, reflection, GenAI spans, durability fix, HotpotQA eval harness, ADR 005, PR | Opus-class (session default) | ~2.5M in (mostly cached), ~70k out | ~$6-9 | |
