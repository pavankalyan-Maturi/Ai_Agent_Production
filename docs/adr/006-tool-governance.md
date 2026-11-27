# ADR 006: Tool governance with mcp-rbac-gateway (Phase 3)

Status: accepted. Date: 2026-11-27.

## Context

The owner asked for their library `mcp-rbac-gateway` to be the zero-trust layer for every tool the agent can call. It covers role allowlists, argument constraints, secret/PII redaction, prompt-injection blocking, human approval and an audit trail. Version 0.2.0 was read from the published wheel (PyPI, uploaded 2026-11-27) before integrating. All the APIs used below exist in that wheel.

Differences from the request, found by reading the source:
- The import name is `mcp_rbac_gateway`, not `mcp_sentinel`.
- There is no built-in audit HTTP endpoint (we build one).
- `@guard` / `Sentinel.call` cannot attach a tenant or call id to the audit row (see Decision).
- The project had no auth dependency to reuse (real auth is Phase 5).

## Decision

**Pin:** `mcp-rbac-gateway>=0.2.0,<0.3`. The audit endpoint reads the 0.2.x `events` table directly.

**Policy: `sentinel.yaml` at the repo root.** `default_action: deny`, and the default agent `anonymous` denies everything.
- `researcher`: `search`, `fetch_doc`, `calculator`, with argument rules:
  - query ≤ 500 chars, no control characters
  - doc_id ≤ 200 chars, no `../`, `/` or control characters
  - the calculator expression must fully match `[0-9+\-*/%(). eE]{1,200}`
- `operator`: inherits researcher and adds `create_ticket`, which always requires approval (title ≤ 120 chars on one line, priority in low/medium/high).
- Scanner: redact secrets in inputs and secrets + PII in outputs, scan inputs for injection, block injection at a score ≥ 0.7, strip invisible characters.
- Audit: arguments are logged (already redacted), results never (PII off).

**Role.** Each run carries `role` in graph state. Like `tenant_id`, it is set by the caller and never by the model. The model is only offered the tools its role can see.

**Enforcement: `agent/governance.py`, used by the graph's `act` node when `governance=` is passed.**
- Each call runs the gateway's own pipeline in its order: `check_call` (policy → input redaction → input injection scan → approval) → tool → `check_result` (output redaction → injection score) → `record`.
- These are the same steps `@sentinel.guard` runs. They are called directly so the audit row gets:
  - `session_id = "tenant:<id>"`, so audit reads are per tenant (project rule)
  - `request_id = <tool_call_id>`
  - the current span's W3C `traceparent`, which links audit rows to traces
- `guard()` has no parameter for these. A `session_id`/`request_id`/`meta` argument on `guard`/`call` would let us use the decorator unchanged (suggested upstream change).

**Denials don't crash the run.** A blocked call becomes the observation `error: tool_denied {"tool", "decision", "reason", "retry": false, "hint"}`.
- The span gets `error.type=tool_denied`, and the model continues.
- When injection is blocked, the model never sees the tool's output.

**Approvals: one decision, a durable pause.**
- The gateway's policy (`require_approval`, `hitl.tools`/`rules`, MCP `destructiveHint`) decides *whether* a call needs a human. The graph's `route` asks `Governance.needs_approval` and sends those calls to `approve`, which calls LangGraph `interrupt()`.
- The pause is a Postgres checkpoint (ADR 004), so it survives restarts and can be approved from another process. The gateway's in-memory broker would lose a pending approval on restart.
- On resume, `Command(resume={"approved": true, "approver": "alice"})` is recorded in state. `execute` hands that approver to the gateway's `CallbackApprover`, so the audit row reads `approved` by `alice`.
- On a rejection, the graph writes a `denied_by_human` row itself, so both paths appear in one audit trail.
- A call that needs approval but did not come through the graph waits on the gateway's `ApprovalBroker` (up to `hitl.timeout_seconds`, then `approval_timeout`). Examples: a direct `Governance.execute`, or a policy rule the graph's check did not see.
- `create_approval_router` resolves those waits at `/api/v1/security/approvals`. Because the broker is in memory, it only sees approvals from runs in the same process (UNVERIFIED across replicas; the graph pause is the multi-replica path).
- `hitl.mode` is `auto_deny` in the file, so using it standalone with the CLI fails closed.

**API.**
- `/api/v1/security/approvals` (the gateway's router) and `/api/v1/security/audit?tenant_id=…&blocked_only=&tool=&limit=` are behind `Authorization: Bearer $PAP_ADMIN_API_KEY`.
- With no key set, they answer 503.
- The admin key is a stopgap until Phase 5 auth.

**Action tool.** `create_ticket` writes to `agent_tickets` (tenant-scoped). It is idempotent on (tenant, title, body), because a crashed `act` step re-runs on resume.

## Measured

See RESULTS Phase 3A. Locally, the gateway adds about 2.1 ms per tool call at p50 (2.6 ms p95), including the audit write. A denied call costs 1.8 ms. An output scan of a 2 kB result takes 0.94 ms. That is small next to a model call (seconds), but not free on hot paths.

## Not done here (PR B / C)

- MCP proxy: the project has no MCP server or client yet. PR B adds our MCP server (P3.1) and puts the gateway's `StatelessHttpProxy` in front of it, preserving `_meta` and `traceparent`. That is where the library's proxy layer belongs.
- A2A card (P3.4) comes in PR C.

## Rejected

- Using the gateway's broker as the only approval path: in memory, lost on restart, one process.
- Keeping `Tool.requires_approval` as the source of truth: it is now an extra hint, and the policy file decides.
- Wrapping each tool function with `@guard`: it is equivalent, but the audit rows would have no tenant.
