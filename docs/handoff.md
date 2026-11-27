# Handoff

## Done
- Phases 0-6 merged (PR #1-#17).
- Phase 4 follow-ups (this PR):
  - `gateway/agent_model.py` `GatewayModel`: wraps any `ChatModel` so the agent graph's model
    node goes through the gateway policy: rpm/tpm buckets (`SyncRateLimiter` in
    `gateway/ratelimit.py`, same keys as `/v1/chat/stream`), bounded wait then `RateLimited`,
    tenant-scoped exact cache of the full `Decision` (tool calls + provider blocks, hit = 0
    tokens). ADR 011 limits section updated.
  - Keycloak compose profile `auth` (26.4.2, realm import `deploy/compose/keycloak/pap-realm.json`,
    client `pap-demo`, `tenant_id=demo`, `aud=pap-api`); api env points JWKS at it.
    `scripts/keycloak_smoke.sh` runs in compose-smoke CI. ADR 009 + SPEC pin updated.

## Verified here
- Gate: ruff, format, mypy, full pytest (7 new tests in tests/gateway/test_agent_model.py,
  real Redis), incl. a two-run agent graph where run 2 makes zero model calls.
- `docker compose config` with `--profile auth`.

## Not verified
- Keycloak image pull and realm import: quay.io is blocked in this sandbox; only the
  compose-smoke CI step (`scripts/keycloak_smoke.sh`) exercises it.

## Open
- Evals still construct `AnthropicModel` directly; wrapping them in `GatewayModel` would change
  eval-gate spend accounting, so it is opt-in and not done.
- Approvals API approver from body + untenanted pending approvals (ADR 009, library <0.3).
- Gate covers the agent eval only; retrieval/security keep their own workflow rules.

## Next step
Phase 7 (vLLM runbook, human-run GPU) in a fresh thread.

## Versions pinned
Keycloak 26.4.2 (new row in docs/SPEC.md). Otherwise unchanged: SPEC Pins table, ADR 002-012.
