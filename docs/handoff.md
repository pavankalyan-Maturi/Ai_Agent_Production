# Handoff

## Done
- Phases 0 and 1 merged (PR #1, #2, #3).
- Phase 2 PR A (this PR, not merged): `agent/`, a LangGraph planner/executor.
  - Tools: search, fetch_doc and calculator. The tenant comes from state.
  - AsyncPostgresSaver checkpointing, with a crash-resume test.
  - Step, token and USD budgets with a degraded partial answer.
  - Approval pause (`interrupt`) for flagged tools.
  - New `PgStore.fetch_doc`.
  - ADR 004.

## Verified here
- Gate: ruff, format, mypy (now including `agent`), 49 unit tests.
- Integration against pgvector 0.8.6 (Postgres 17): kill/resume, HITL across savers, tenant-scoped tools. All passed.
- Bench: 10/10 kill/resume trials, ≈2.2 ms checkpoint overhead per super-step (RESULTS Phase 2A).

## Not verified
- `AnthropicModel` against the live API: no calls made. Planning quality with a real model: NOT MEASURED.
- Checkpointer behind PgBouncer.

## Open
- No checkpoint pruning. No HTTP endpoint for runs or approvals yet.
- OTel GenAI semconv pin is deferred to PR B, when agent spans land.

## Next step
Review and merge PR A. Then Phase 2 PR B: P2.4 compaction + token chart, P2.5 experience memory + admission gate with the HotpotQA off/on/gated ablation, P2.6 grounded reflection.

## Versions pinned
docs/SPEC.md Pins table, ADR 002-004 (langgraph 1.2.24, checkpoint 4.3.2, checkpoint-postgres 3.2.1, prebuilt 1.1.3).
