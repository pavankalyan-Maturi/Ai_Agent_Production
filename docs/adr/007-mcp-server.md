# ADR 007: Stateless MCP server behind mcp-rbac-gateway (Phase 3 PR B)

Status: accepted. Date: 2026-11-27.

## Context
P3.1/P3.2 ask for an MCP server that speaks spec 2026-07-28 (stateless Streamable HTTP) and works with SDK v2 clients from both eras. It must scale across replicas with no session store and use OAuth as the spec requires. The owner also asked that external MCP traffic pass through their library's proxy (ADR 006), with `_meta` and `traceparent` preserved.

## Decision
**Pin.** `mcp[cli]==2.3.*` (2.3.0). SPEC originally pinned `2.0.*`, but pip-audit in CI flagged five CVEs in 2.0.1 (CVE-2026-59951, -115563, -116684, -116685, -116686), fixed in 2.2.0. The pin moves to the latest minor, 2.3, and this ADR records the change. With 2.3, `AuthSettings.validate_token_resource=True` is set, so the SDK also checks the token's resource on top of our `aud` check. The SDK serves both 2026-07-28 (`server/discover`, per-request `_meta`) and the 2025-11-25 `initialize` handshake from the same endpoint.

**Server (`mcp_server/server.py`).** `MCPServer("pap-retrieval")` exposes two tools, `search` and `fetch_doc`. They are the agent's own retrieval tools, annotated read-only and non-destructive.
- Transport: `streamable_http_app(stateless_http=True, json_response=True)` with a Host allowlist (DNS-rebinding protection).
- No session ids and no per-process state. That is why any replica can serve any request.

**Auth (`mcp_server/auth.py`).** The server is an OAuth 2.1 resource server only. It verifies tokens and never issues them.
- `JwtVerifier` uses PyJWT and requires:
  - `exp`, `iss`, `sub`
  - `aud` equal to this server's resource URL (RFC 8707 audience binding)
  - a `tenant_id` that matches the tenant regex
  - the scope `pap:search` (otherwise 403)
- The SDK serves RFC 9728 protected-resource metadata and returns 401 with `WWW-Authenticate: Bearer ... resource_metadata=...`.
- **The tenant comes only from the token.** A `tenant_id` argument is ignored. This is the project's per-tenant isolation rule.
- Keys come from env (`PAP_MCP_JWT_KEY`, HS256 by default). RS256/ES256 works by giving a public key PEM and `PAP_MCP_JWT_ALGORITHMS`. JWKS fetching is not built (Phase 5 auth).

**Gateway (`mcp_server/gateway.py`).** It runs mcp-rbac-gateway's `StatelessHttpProxy` (0.2.0) as its own app. Agents reach only the gateway; the server stays internal.
- **Role.** The role comes from `X-Sentinel-Token`, matched against `token_env` in `sentinel.yaml`: `PAP_MCP_RESEARCHER_TOKEN` and `PAP_MCP_OPERATOR_TOKEN`.
  - With no token the caller is `anonymous` and sees and may call nothing.
  - A wrong token gets 401.
- **What it checks.** The same policy as in-process (ADR 006): allowlists, argument rules, redaction, and injection blocking on outputs.
  - It also checks headers against the body. A mismatched `Mcp-Name`/`Mcp-Method` gets 400 with JSON-RPC error `-32020` before anything is forwarded.
  - Every call is audited with `transport=http-stateless` and the request's `traceparent`.
- **What it forwards.** `Authorization` (the OAuth token) and `params._meta` (protocol version, client info, `traceparent`) go upstream unchanged. Only `arguments` can be rewritten, by redaction. `X-Sentinel-Token` is not forwarded.
- **Approvals.** The gateway mounts the library's `/approvals` router. A token is required (`PAP_MCP_APPROVAL_TOKEN`); without one the gateway refuses to start, because the router would otherwise be open.
- **Entry points.** `uvicorn --factory mcp_server.app:server_app` (internal) and `uvicorn --factory mcp_server.app:gateway_app` (public).

## Measured
In-process, with no sockets and a tool that costs nothing (`scripts/bench_mcp.py -n 1000`):

| Path | p50 | p95 |
|---|---|---|
| Direct to the server | 2.63 ms | 3.70 ms |
| Through the gateway | 6.07 ms | 7.85 ms |

So the gateway adds about 3.4 ms at p50. That includes its own JSON-RPC parse, a second HTTP hop, scans and the audit write. Details are in RESULTS, Phase 3B.

## Limits and gaps (flagged)
- **2-replica test.** It is in-process: two independently built apps behind a round-robin transport with no affinity. The client's discover, list and calls were spread across both, and every call succeeded. The kind/Kubernetes e2e from SPEC's acceptance list is NOT RUN here (no cluster in this sandbox).
- **Protocol era through the gateway.** `StatelessHttpProxy` supports only 2026-07-28. A 2025-era client must talk to the server directly or through the library's `HttpProxy`. Our gateway is modern-only.
- **Audit tenant at the gateway.** Gateway audit rows have no tenant, because the library's proxy writes `session_id=None`. So `/api/v1/security/audit?tenant_id=` does not show them yet. This was added to the upstream change list (`/mnt/project-files/phase3/mcp-rbac-gateway-0.3-prompt.md`).
- **Two credentials.** Callers send both the OAuth token (tenant) and `X-Sentinel-Token` (role). Taking the role from a JWT claim would need the library to accept a role resolver.
- **Approvals across gateway replicas.** The library's broker lives in memory per process (ADR 006). Our two MCP tools are read-only, so nothing needs approval there today.

## Rejected
- **Stateful Streamable HTTP with a session store.** The spec's stateless mode removes the need for one.
- **Putting the tenant in a header or argument.** It is forgeable.
- **Enforcing roles inside the server as well.** It would duplicate the policy. The gateway is the single enforcement point, and the server is not exposed.
