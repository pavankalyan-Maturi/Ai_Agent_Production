# Handoff

## Done
- Phases 0-2 merged (PR #1-#5).
- Phase 3 PR A (this PR, not merged): tool governance with the owner's `mcp-rbac-gateway` 0.2.0 (ADR 006).
  - `sentinel.yaml`: roles `anonymous` (deny all), `researcher` (read-only), `operator` (+ `create_ticket`, always needs approval). It adds argument rules, secret/PII redaction and injection blocking.
  - `agent/governance.py`: every tool call in the graph runs policy → input scan → approval → tool → output scan → audit. A blocked call becomes a structured `tool_denied` observation, never a crash.
  - Approvals: the policy decides, and the LangGraph interrupt waits durably. The approver is recorded in the gateway's audit. Calls made outside the graph wait on the gateway broker.
  - API: `/api/v1/security/approvals` (the gateway's router) and `/api/v1/security/audit` (tenant-scoped), both behind `PAP_ADMIN_API_KEY`.
  - New action tool `create_ticket` (tenant-scoped, idempotent).

## Spec deviations (owner request vs library 0.2.0)
- The import is `mcp_rbac_gateway`, not `mcp_sentinel`.
- `@guard` can't carry the tenant or call id, so we call the same pipeline steps directly.
- There is no audit HTTP endpoint in the library, so we wrote one that reads the `events` table.
- No auth existed, so an admin bearer key was added as a stopgap until Phase 5.
- No external MCP connections exist yet, so the proxy comes in PR B.

## Verified here
- Gate: ruff, format, mypy, full unit suite including 13 new governance tests.
- Integration against local pgvector: the ticket tool is idempotent and tenant-scoped.
- Bench: about 2.1 ms p50 per governed call.

## Not verified
- The approval broker across several API replicas. It lives in memory; the graph pause is the multi-replica path.
- The Docker image with sentinel.yaml (not built this thread).

## Next step
Review and merge PR A. Then PR B: our stateless MCP server (P3.1/P3.2) behind the gateway's `StatelessHttpProxy`, keeping `_meta`/`traceparent`. Then PR C: the A2A card (P3.4) and P3.3.

## Versions pinned
docs/SPEC.md Pins table, ADR 002-006 (`mcp-rbac-gateway>=0.2.0,<0.3`).
