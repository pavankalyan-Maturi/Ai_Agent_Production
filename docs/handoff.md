# Handoff

## Done
- Phases 0, 1 and Phase 2 PR A merged (PR #1-#4).
- Phase 2 PR B (this PR, not merged):
  - P2.4 context compaction plus a token chart.
  - P2.5 experience memory (`agent_memory`, tenant-scoped) with an admission gate (static checks, then a replay with and without the item).
  - P2.6 reflection triggered only by external checks (citations, tool errors).
  - OTel GenAI spans with no content, pinned to semconv schema 1.44.0.
  - HotpotQA off/on/gated ablation on Haiku 5.5 (`eval-agent` workflow, $2 cap, response cache, eval gate). ADR 005.
- Fix to PR A: checkpoints are now written with `durability="sync"`. The default `async` lost the checkpoint in 8 of 30 kill trials (ADR 004 amended).

## Verified here
- Gate: ruff, format, mypy (apps, retrieval, evals, agent), 70 unit tests.
- Integration against pgvector 0.8.6: kill/resume 30/30 with sync, HITL across savers, tenant-scoped tools and memory bank.
- Eval plumbing end to end against real Postgres with a scripted model.

## Not verified
- The Haiku ablation numbers come from CI (see RESULTS Phase 2B).
- HotpotQA download: the sandbox cannot reach Hugging Face, so CI is the first real run.
- Sonnet with thinking on, after compaction drops raw blocks.
- Checkpointer behind PgBouncer.

## Open
- No feedback endpoint for production memory outcomes (Phase 5). No memory eviction. No checkpoint pruning.

## Next step
Review the ablation table in this PR's `eval-agent` run, then merge. Phase 3 (MCP + A2A) on request.

## Versions pinned
docs/SPEC.md Pins table, ADR 002-005 (langgraph 1.2.24 family; opentelemetry-semantic-conventions 0.66b2 / schema 1.44.0).
