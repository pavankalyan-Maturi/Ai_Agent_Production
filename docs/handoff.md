# Handoff

## Done
- Phases 0-2 and Phase 3 PR A and B merged (PR #1-#7).
- Phase 3 PR C (this PR, not merged), ADR 008:
  - **P3.4 A2A** (`agent_a2a/`): JWS ES256-signed agent card at `/.well-known/agent-card.json` with a JWKS. A client-side verifier pins the trusted `jku` and accepts only ES256.
    - JSON-RPC at `/a2a/v1` runs the LangGraph agent as the token's tenant, in the read-only role and still governed.
    - Tasks are scoped by owner (`tenant:sub`).
  - **P3.3 tool retrieval** (`agent/tool_retrieval.py`, flag `PAP_TOOL_RETRIEVAL_RAGMCP`, off): top-k tool selection after the RBAC filter, which keeps tools already used.
    - Scaling eval in `evals/tools/`, plus a CI workflow `eval-tools` (fastembed, no API spend).
  - `agent_a2a` was added to mypy (CI and the CLAUDE.md gate) and to the Dockerfile.

## Verified here
- Gate: ruff, format, mypy and 123 unit tests, including 10 A2A and 5 tool-retrieval tests.
- Integration against local pgvector passes. pip-audit is clean.
- Tool scaling with the hash embedder: at 163 tools, recall@10 is 0.969 and spec tokens drop from 4,858 to about 295.

## Not verified
- Tool scaling with fastembed. It runs in CI `eval-tools`; read the numbers from that job.
- End-to-end task success with tool retrieval on a real model (NOT MEASURED).
- A2A with a production model and a persistent task store. There is no runnable entry yet (Phase 4 LiteLLM).
- The kind/Kubernetes e2e for Phase 3 (no cluster here).

## Open
- Library 0.3 follow-up: a minimal change to test it, when the owner ships it. The upstream list is `/mnt/project-files/phase3/mcp-rbac-gateway-0.3-prompt.md`.
- A2A: DatabaseTaskStore on Postgres, streaming, and key pinning or x5c.

## Next step
Review and merge PR C. That closes Phase 3 (checkpoint: protocol conformance review). Phase 4 on request.

## Versions pinned
docs/SPEC.md Pins table, ADR 002-008 (`a2a-sdk>=1.1,<2` → 1.3.1, `mcp[cli]==2.3.*`, `mcp-rbac-gateway>=0.2.0,<0.3`).
