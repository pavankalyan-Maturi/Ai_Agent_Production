# Handoff

## Done
- Phases 0-3 and 5 merged (PR #1-#10). Phase 4 (this PR, not merged), ADR 011:
  - **P4.1** LiteLLM 1.110.0 in our own image (`deploy/litellm/Dockerfile`, uid 10001, read-only,
    no port), `gateway/litellm.yaml`: aliases, retries, fallbacks, `allowed_routes`, admin UI off.
  - **P4.2** `gateway/ratelimit.py` (Redis Lua token buckets per tenant+sub, rpm+tpm, 429 +
    Retry-After); `gateway/cache.py` (tenant-scoped exact + semantic with salient-token guard);
    `gateway/cache_pairs.py` false-hit set; fallbacks in LiteLLM.
  - **P4.3** `POST /v1/chat/stream` (scope `pap:chat`) in `apps/api/chat.py`; disconnect closes
    the upstream stream. `gateway/client.py`: `GatewayModel` (agent ChatModel) + `StreamClient`.
  - **P4.4** `loadtest/locustfile.py` + runbook; `scripts/bench_gateway.py`.
  - `gateway/mock_llm.py`: OpenAI-compatible mock (vLLM stand-in). CI `lint-type-test` gets a
    Redis service; compose gets `mock-llm` + `litellm`; smoke checks fallback + admin 403.

## Verified here
- Gate: ruff, format, mypy (+gateway), 156 unit tests (11 new, real Redis + real uvicorn mock).
- LiteLLM 1.110.0 run locally: fallback, admin routes 403, disconnect propagates to upstream.
- Locust 50 users, 60 s: miss first-token p50 87 / p95 190 ms, exact hit p50 5 ms, 0 failures.
- Cache false hits at 0.9: 5/20 without guard, 0/20 with. Numbers in RESULTS.md.

## Not verified
- Docker images and compose-smoke (`pap-litellm:dev` build, healthcheck): CI compose-smoke.
- Real providers through LiteLLM (no paid calls this thread); semantic cache with bge-small.
- Nothing open from PR B: Haiku arms measured in CI (camel ASR 0%, benign acc 85%), recorded in
  RESULTS.md and ADR 010.

## Open
- Agent graph does not use cache/limiter yet (only the SSE route); TPM charged on an estimate.
- Semantic guard misses entity/metric swaps; fastembed not in the api image (exact-only there).
- From before: sinks into sentinel.yaml, Keycloak compose profile, HotpotQA memory gate red.

## Next step
Review this PR and compose-smoke; optionally run `loadtest/README.md`. Then Phase 6.

## Versions pinned
docs/SPEC.md Pins table (LiteLLM 1.110.0, locust 2.47.0 via uvx, httpx made explicit), ADR 002-011.
