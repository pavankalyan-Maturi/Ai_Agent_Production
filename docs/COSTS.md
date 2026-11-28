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
| 2026-11-27 | P2B | Anthropic API (CI eval, not Claude Code): HotpotQA memory ablation, 2 runs, ~2,200 Haiku 5.5 calls | claude-haiku-5-5 | measured by the eval spend tracker | $0.48 | |
| 2026-11-27 | P3A | mcp-rbac-gateway governance: policy, guarded tools, approvals/audit API, tests, bench, ADR 006, PR | Opus-class (session default) | ~1.5M in (mostly cached), ~45k out | ~$4-6 | |
| 2026-11-27 | P3B | Stateless MCP server (OAuth RS, both eras), mcp-rbac-gateway StatelessHttpProxy in front, 24 tests, bench, ADR 007, PR | Opus-class (session default) | ~1.5M in (mostly cached), ~40k out | ~$4-6 | |
| 2026-11-27 | P3C | A2A signed card + JSON-RPC over the agent, tool retrieval flag + scaling eval, 15 tests, ADR 008, PR | Opus-class (session default) | ~1.5M in (mostly cached), ~40k out | ~$4-6 | |
| 2026-11-27 | P5A | OIDC auth, PII edges, spotlighting, injected-doc suite + eval-security CI, 16 tests, ADR 009, PR | Opus-class (session default) | ~1.2M in (mostly cached), ~35k out | ~$3-5 | |
| 2026-11-27 | P5B | Taint ledger, plan/data separation (quarantined extract), step + trace tool-call policy, 70-case suite + 2 arms, 7 tests, ADR 010, PR | Opus-class (session default) | ~0.8M in (mostly cached), ~30k out | ~$2-4 | Haiku eval in CI, cap $1 |
| 2026-11-27 | P4 | LiteLLM 1.110.0 hardened image + config, Redis rate limits, tenant cache + false-hit set, SSE + disconnect, Locust run, 11 tests, ADR 011, PR | Opus-class (session default) | ~1.0M in (mostly cached), ~35k out | ~$2-4 | no paid model calls |
| 2026-11-27 | P2 fix | PR #13: eval-agent gates the shipped config, memory arms report-only (row added later) | Opus-class (session default) | not recorded | ~$1-2 (ESTIMATE) | |
| 2026-11-27 | P6A | Trajectory metrics, MAST tags, Haiku judge + kappa, paired-bootstrap eval-gate, 7 tests, ADR 012, PR | Opus-class (session default) | ~0.8M in (mostly cached), ~30k out | ~$2-3 | Haiku in CI: $0.003 measured (agent fully cached) |
| 2026-11-27 | P6.2 | Labels xlsx -> CSV, kappa from CI, per-question baseline, 2 small PRs | Opus-class (session default) | ~0.4M in (mostly cached), ~10k out | ~$1 | Haiku $0.000 (cache) |
| 2026-11-27 | P4 follow-up | GatewayModel (agent graph behind cache + limiter), SyncRateLimiter, Keycloak `auth` profile + CI smoke, 7 tests, PR | Opus-class (session default) | ~0.5M in (mostly cached), ~15k out | ~$1-1.5 (ESTIMATE) | no paid model calls (eval-gate not triggered: no agent/evals/retrieval paths) |
| 2026-11-27 | P7.1-7.2 | vLLM serve config (4 arms), streaming bench, LiteLLM hosted_vllm entry + compose smoke, runbook, 5 tests, ADR 013, PR | Opus-class (session default) | ~0.3M in (mostly cached), ~12k out | ~$1 (ESTIMATE) | no paid model calls; GPU run not done |
| 2026-11-27 | P7.2 fix | GPU-run walkthrough; vLLM 0.34 flag fix, native (no-Docker) launch, run_all.sh, 2 tests, PR | Opus-class (session default) | ~0.2M in (mostly cached), ~8k out | ~$0.5 (ESTIMATE) | no paid model calls |
| 2026-11-27 | P8 | AWS burst as code: Terraform EKS + k3s, Helm chart, Argo CD, KEDA, Chaos Mesh, pgBackRest drill, up/destroy scripts, runbook + cost guard, infra CI, 8 tests, ADR 014, PR | Opus-class (session default) | ~0.6M in (mostly cached), ~30k out | ~$1.5-2 (ESTIMATE) | no paid model or AWS calls |
| 2026-11-28 | P7.2 fix 2 | Runbook gaps from the real RunPod run (tmux env, vllm venv, fail-fast preflight), 1 test, PR | Opus-class (session default) | ~0.15M in (mostly cached), ~5k out | ~$0.3 (ESTIMATE) | no paid model calls |
| 2026-11-28 | P7.3 | Read the user's GPU run, wrote the RESULTS table + ADR 013 update, committed the raw results, PR | Opus-class (session default) | ~0.15M in (mostly cached), ~6k out | ~$0.3 (ESTIMATE) | GPU: RunPod L40S $1.09/h, paid by the user |
| 2026-11-28 | P8.3 prep | AWS burst walkthrough; preflight script, env-file + WSL + amd64 runbook fixes, 2 tests, PR | Opus-class (session default) | ~0.25M in (mostly cached), ~15k out | ~$0.5-1 (ESTIMATE) | no paid model or AWS calls |
