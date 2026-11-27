# Handoff

## Done
- Phases 0-3 merged (PR #1-#8). Phase 4 (gateway) NOT built: the user chose Phase 5 first.
- Phase 5 PR A (this PR, not merged), ADR 009:
  - **P5.0 auth** (`apps/api/auth.py`): OIDC resource server. JWKS or a static key; `iss`, `aud`, `exp`, `sub`, `tenant_id` required.
    - `pap:admin` guards `/api/v1/security/*`, and `pap:ingest` guards `/v1/ingest`. Tenant from the token only.
    - The admin key and the `X-Tenant-Id` header are removed. With no key configured the routes answer 503.
  - **P5.1** `guardrails/pii.py`: the question is redacted before the model, memory or a checkpoint; the answer is redacted on the way out. Uses the library's Scanner.
  - **P5.2** `guardrails/spotlight.py`: delimiting for `search`/`fetch_doc` output, opt-in via `build_graph(guardrails=...)`.
    - Injected-doc suite `evals/security/` (40 attacks in 8 families, 20 benign). CI `eval-security` runs Haiku ASR per arm.
  - `guardrails` added to mypy (CI + CLAUDE.md gate) and to the Dockerfile.

## Verified here
- Gate: ruff, format, mypy, 139 unit tests (16 new: auth, guardrails, eval harness).
- Offline security eval: the scanner detects 25% of the 40 injected docs (direct_override and fake_markup only), with 0/20 false blocks. Governed call p50 1.47 ms.

## Not verified
- Haiku ASR per arm (CI `eval-security`, < $0.10).
- Keycloak itself: no compose profile yet. Tokens in tests are minted locally (RS256).

## Open
- PR B: P5.3 plan/data separation + taint tags (CaMeL-lite) and P5.4 step-level + trace-level checks, with false-block and latency numbers.
- Keycloak compose profile (dev realm, `tenant_id` mapper). Approvals: the approver comes from the token, not the body, and the pending list is tenant-filtered (library broker is global).
- Merge mcp_server's JwtVerifier into apps/api/auth. Phase 4 gateway. HotpotQA memory gate red on main (separate task).

## Next step
Review PR A. Then PR B in a fresh thread.

## Versions pinned
docs/SPEC.md Pins table, ADR 002-009 (PyJWT 2.15.1, mcp-rbac-gateway 0.2.0).
