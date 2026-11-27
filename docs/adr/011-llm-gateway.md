# ADR 011: LLM gateway: LiteLLM for routing, tenant-scoped cache and rate limits in the API (Phase 4)

Status: proposed (Phase 4 PR).

## Context
Until now the agent called the Anthropic SDK directly. Phase 4 needs one choke point for
provider keys, retries, fallbacks and (Phase 7) vLLM, plus per-key rate limits, exact and
semantic caching and an SSE endpoint. Every cache key and query must carry the tenant id.

## Decision
1. **LiteLLM proxy 1.110.0** (`gateway/litellm.yaml`, `deploy/litellm/Dockerfile`) owns model
   aliases (`agent-default`, `agent-fast`, `mock*`), retries and fallbacks. Built from PyPI on
   `python:3.12-slim`, uid 10001, read-only rootfs, `cap_drop: ALL`, no DB, no published port.
   `allowed_routes` limits it to chat completions, models and health; `DISABLE_ADMIN_UI`,
   `NO_DOCS`, `NO_REDOC`, `turn_off_message_logging` (no prompts in proxy logs).
2. **Rate limits in the API** (`gateway/ratelimit.py`): token buckets in Redis, one Lua script
   per check using Redis `TIME`, keyed `rl:{tenant}:{sub}:{rpm|tpm}`. TPM cost is estimated
   before the call (chars/4 + max_tokens). 429 carries `Retry-After`.
3. **Cache in the API** (`gateway/cache.py`): exact (sha256 of model, messages, params) under
   `llm:exact:{tenant}:`; semantic only for single-turn, temperature-0, tool-less prompts,
   per (tenant, model, system, params) namespace, <=256 entries scanned with numpy. A semantic
   hit also needs identical salient tokens (numbers, negations, quoted strings). Only completed
   streams are written.
4. **SSE** `POST /v1/chat/stream` (scope `pap:chat`): `meta`/`delta`/`error`/`done` events.
   Client disconnect cancels the response task; leaving `StreamClient.stream` closes the
   upstream connection, and LiteLLM closes its upstream in turn (measured, see RESULTS).
5. `GatewayModel` gives the agent the same `ChatModel` interface over LiteLLM. CI still uses
   `ScriptedModel`; `gateway/mock_llm.py` is the OpenAI-compatible stand-in for vLLM.

## Version and CVE check (2026-11-27, PyPI JSON `vulnerabilities`; GHSA/OSV APIs blocked here)
- 1.111.0 (2026-11-25) and 1.110.0 (2026-11-18): 0 known advisories. 1.107.0 is no longer on
  PyPI and stays banned (SPEC). 1.97.0 has GHSA-7hp6-4w63-5g45 (fixed 1.100.4+).
- Pinned **1.110.0**, not the 2-day-old 1.111.0: a one-week cooldown on a package with a
  supply-chain incident in its history. Re-check with pip-audit/trivy in `security.yml`.

## Alternatives
- LiteLLM's own Redis cache and virtual-key limits: its cache key has no tenant id, and virtual
  keys need its Postgres schema, master-key admin flows and the admin UI we are turning off.
  Our tenant comes from the OIDC token (ADR 009), so limits key off that instead.
- Upstream `ghcr.io/berriai/litellm` image: root by default, bundles UI/DB tooling, and ghcr is
  unpullable from this sandbox. Own Dockerfile keeps the surface small.
- RedisVL / Redis vector search for the semantic cache: needs Redis Stack; at <=256 entries per
  namespace a numpy scan costs ~1.6 ms p50 and no new module.
- Envoy AI Gateway / Kong: more moving parts with no feature we use over LiteLLM today.

## Tradeoffs and limits
- Semantic cache guard does not catch entity or metric swaps ("ACME" vs "Globex" revenue): at
  threshold 0.8 those were 10/20 false hits with the guard on. Default 0.9 had 0/20 on our
  lexical embedder; real-embedder numbers NOT MEASURED (fastembed weights not downloadable here).
- Semantic cache needs fastembed in the image; without it the API runs exact-only (logged).
- TPM is charged on an estimate, not actual usage; no refund on short completions.
- The static LiteLLM UI bundle is still served at `/ui` with `DISABLE_ADMIN_UI`; its API calls
  are 403 via `allowed_routes`. Mitigated by no published port.
- Agent graph (follow-up PR): `gateway/agent_model.py` `GatewayModel` wraps any `ChatModel`
  with the same rpm/tpm buckets (`SyncRateLimiter`, shared keys with `/v1/chat/stream`) and a
  tenant-scoped exact cache of the whole `Decision` (tool calls + provider blocks). A hit reports
  0 tokens. No semantic cache on agent turns (tool traffic). Bound to one tenant + caller per run
  because `decide()` carries no principal; exact cache at temperature > 0 replays one sample,
  which is the intended trade for cost. Not yet wired into evals (would change eval-gate spend
  accounting); callers opt in.
