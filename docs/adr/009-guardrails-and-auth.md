# ADR 009: Guardrails at the graph edges, spotlighting, and OIDC auth (Phase 5 PR A)

Status: proposed (PR A of Phase 5). PR B adds plan/data separation, taint and trace policy.

## Context
- mcp-rbac-gateway (ADR 006) already guards every tool call: RBAC, argument rules, secret/PII
  redaction of arguments and results, a heuristic injection score with `action: block` at 0.7.
- Three gaps remained. The user turn and the final answer never pass the gateway. Injections the
  heuristics miss reach the model unmarked. The security API used a shared admin key, and ingest
  took the tenant from an `X-Tenant-Id` header.

## Decision
1. **P5.1 PII at the edges** (`guardrails/pii.py`). The question is redacted in `start`, before
   the model, experience memory or a checkpoint sees it. The final answer is redacted in `plan`,
   and its raw provider blocks are dropped so the checkpoint holds no copy. The detectors are the
   library's `Scanner` (16 built-ins), so tool I/O and the edges share one rule set. Only
   detector names are kept (`state.redactions`), never values.
2. **P5.2 spotlighting** (`guardrails/spotlight.py`, Hines et al. 2024, delimiting). Output of
   the listed data tools (`search`, `fetch_doc`) is wrapped in `<<untrusted_data>>` markers. The
   system prompt declares marked text as data. Markers inside the payload are stripped first, so
   a document cannot close the block early. This is opt-in per graph (`guardrails=`).
3. **Injected-doc suite** (`evals/security/`): 8 attack families x 5 = 40 poisoned docs and 20
   benign docs that look risky. Grading is a per-case canary in the answer or any tool argument.
   Arms: none / scanner / scanner+spotlight. CI job `eval-security`, Haiku 5.5, cached, <$0.10.
4. **OIDC resource server** (`apps/api/auth.py`). Keycloak is the IdP (the project keeps one:
   Keycloak, not Cognito, because it runs the same in compose, kind and EKS). The API verifies
   tokens, never issues them. It requires `iss`, `aud`, `exp`, `sub` and `tenant_id`. Scopes:
   `pap:admin` for `/api/v1/security/*`, `pap:ingest` for `/v1/ingest`. The tenant always comes
   from the token: the audit endpoint ignores a `tenant_id` parameter, and the ingest header is
   gone. Keys come from the JWKS URL (PyJWKClient, kid-selected, cached), or one static key in
   tests. Neither set = 503, so auth is never silently off. The verifier refuses `none` and
   refuses HS* with a JWKS (public-key-as-HMAC forgery).

## Alternatives
- **Presidio / an NER model for PII.** Better recall on names and addresses. It costs a spaCy
  model in the image and ~10-50 ms per call. We'd also lose the single rule set with the gateway.
  Revisit if names in answers matter to a tenant.
- **An LLM injection classifier** (Prompt Guard, Llama Guard). It would raise detection on
  paraphrase and multilingual attacks, at a model call per document, and it is itself injectable.
  The structural fix is PR B (plan/data separation), so this classifier stays out unless PR B
  numbers say otherwise.
- **Datamarking or base64 encoding** (the other spotlight variants). They are stronger in the
  paper but hurt the model's reading of citations. Delimiting is the cheapest first step.
- **Cognito.** It works only on AWS, so compose and kind would need a second IdP.
- **Keep the admin key next to JWT.** Two auth paths means two things to audit. Removed.

## Measured (offline, this sandbox, `uv run python -m evals.security.run`)
- Gateway scanner: detected **10/40 (25%)** injected docs, **0/20** false blocks. Only
  `direct_override` and `fake_markup` were caught (100% each). Role override, tool exfil,
  paraphrase, authority, multilingual and base64 were all **0%**. Keyword heuristics alone are
  not a defence, which is why PR B adds the structural one.
- Governed tool call p50 1.47 ms / p95 1.94 ms (n=300; bare call ~0 ms). PII redaction p50
  0.04 ms per document.
- Model ASR per arm: from CI `eval-security` (Haiku 5.5). NOT MEASURED here (no API access).

## Consequences
- Every API client needs a token. Compose smoke doesn't call these routes. A Keycloak compose
  profile with a dev realm and the `tenant_id` mapper is a follow-up: UNVERIFIED until it runs.
- The approvals API still takes the approver name from the request body, and pending approvals
  are not tenant-filtered (the library's broker is global, pinned <0.3). Both are open.
- mcp_server keeps its own JwtVerifier (ADR 007). Merging the two verifiers is a small follow-up.

## Pins
PyJWT 2.15.1 (`jwt.PyJWKClient`), mcp-rbac-gateway 0.2.0, Keycloak 26.x (when the profile lands).
