# Handoff

## Done
- Phases 0-2 and Phase 3 PR A merged (PR #1-#6).
- Phase 3 PR B (this PR, not merged): stateless MCP server behind mcp-rbac-gateway (ADR 007).
  - `mcp_server/server.py`: MCPServer with `search` and `fetch_doc` (read-only).
    - Transport: stateless Streamable HTTP, JSON responses, Host allowlist.
    - Serves 2026-07-28 and 2025-11-25 clients.
  - `mcp_server/auth.py`: OAuth resource server.
    - Verifies the JWT: aud is the resource URL, plus iss, exp, scope `pap:search` and `tenant_id`.
    - The tenant comes only from the token.
    - Serves RFC 9728 metadata and returns a 401 challenge.
  - `mcp_server/gateway.py`: the library's `StatelessHttpProxy` in front.
    - The role comes from `X-Sentinel-Token` (new `token_env` entries in sentinel.yaml).
    - Forwards `_meta`, `traceparent` and `Authorization` unchanged.
    - Requires an approval token.
  - Entry points: `uvicorn --factory mcp_server.app:server_app` and `mcp_server.app:gateway_app`.
  - `mcp_server` was added to mypy (CI and the CLAUDE.md gate) and to the Dockerfile.

## Verified here
- Gate: ruff, format, mypy (now including mcp_server), and the full unit suite including 23 MCP tests.
- Integration against local pgvector: 6 passed, including real search through the gateway, tenant-scoped.
- Bench: about 3.4 ms p50 added by the gateway, in-process.

## Not verified
- Two replicas on kind/Kubernetes. Only the in-process round-robin test ran.
- The gateway with 2025-era clients. The library's stateless proxy is modern-only.
- JWKS / an RS256 identity provider.
- The compose services for the MCP server and gateway. None were added; the uvicorn commands are in the README.

## Open
- Gateway audit rows carry no tenant (a library gap). This and the other library gaps are in /mnt/project-files/phase3/mcp-rbac-gateway-0.3-prompt.md, section 9.
- When library 0.3 ships, the owner wants a minimal follow-up here that only tests it.

## Next step
Review and merge PR B. Then PR C: A2A card (P3.4, JWS-signed) and P3.3 tool retrieval (RAG-MCP flag and tool-count eval).

## Versions pinned
docs/SPEC.md Pins table, ADR 002-007 (`mcp[cli]==2.3.*` → 2.3.0, moved from 2.0.* for CVEs, `mcp-rbac-gateway>=0.2.0,<0.3`).
