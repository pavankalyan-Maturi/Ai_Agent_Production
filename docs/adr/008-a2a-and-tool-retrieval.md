# ADR 008: A2A agent card + JSON-RPC, and tool retrieval (Phase 3 PR C)

Status: accepted. Date: 2026-11-27.

## A2A (P3.4)

**Pin.** `a2a-sdk[http-server,signing]>=1.1,<2` (SPEC). Installed version: 1.3.1. Spec 1.0, JSON-RPC binding.

**Package name.** `agent_a2a/`, not `a2a/` as in SPEC's layout. A top-level `a2a/` directory would shadow the SDK's `a2a` import.

**Card.** Served at `GET /.well-known/agent-card.json` via the SDK's `create_agent_card_routes`, with ETag and `Cache-Control: public, max-age=300`. It contains:
- `supported_interfaces`: one JSONRPC interface at `/a2a/v1`, protocol version 1.0.
- One skill, `grounded-qa`.
- `security_schemes`: HTTP Bearer, JWT format.
- `security_requirements`: `bearer` with scope `pap:agent`.
- Capabilities: no streaming, no push.

**Signing.**
- The card is signed with JWS ES256 over the SDK's RFC 8785 canonical form (`create_agent_card_signer`).
- The protected header carries `kid`, `alg`, `jku` and `typ=JOSE`.
- The private key comes from env (`PAP_A2A_SIGNING_KEY`, an EC P-256 PEM) and is checked to be P-256.
- The public key is served as a JWKS at `/.well-known/jwks.json` and contains no `d` (private component).

**Verification (client side).** `verifier_from_jwks(keyset, trusted_jku=...)` wraps the SDK's `create_signature_verifier`:
- It accepts only `ES256`, which blocks algorithm confusion.
- It accepts only keys whose `kid` is in the JWKS the client fetched from a `jku` it trusts.
- A card signed with a different key under the same `kid` fails, and so does a card naming another `jku`.
- Tests run this through the SDK's `A2ACardResolver.get_agent_card(signature_verifier=...)`. They cover tampering (the interface URL redirected), a missing signature, a forged key and an untrusted jku.
- **Trust bootstrap:** the client must already know which `jku` to trust (out of band, e.g. config). Fetching the JWKS from the same host proves the card is consistent with that host, not who runs the host. This is the same limit as any TOFU/JWKS scheme. Pinning the key or a CA-backed `x5c` chain is the stronger option (NOT DONE).

**JSON-RPC.** `POST /a2a/v1` uses the SDK's `DefaultRequestHandler` with our `GraphExecutor`. Each task is one LangGraph run: task → working → artifact `answer` → completed, or failed with the stop reason.
- **Auth:** Starlette `AuthenticationMiddleware` with the same JWT verifier as MCP (ADR 007), but the audience is the A2A URL. A token for MCP is rejected (401), and a token without the `pap:agent` scope gets 403.
- **Public routes:** the card and JWKS need no token.
- **Tenant:** comes only from the token. The task store's owner is `tenant:sub`, so `GetTask` with another tenant's task id returns "not found".
- **Role:** A2A callers run as `researcher` (read-only), and tool calls still go through mcp-rbac-gateway governance (ADR 006).
- **Approval pause:** cannot happen for that role; if it did, the task fails closed instead of hanging.

**Not done / limits.**
- Task store: in memory (the SDK's), so tasks are lost on restart and not shared across replicas. The SDK has a `DatabaseTaskStore` (SQLAlchemy) that can be swapped in later. UNVERIFIED with our Postgres.
- Not wired to a production model: there is no runnable entry point yet, because the graph needs the Phase 4 LiteLLM model. Tests use the scripted model.
- No streaming and no push notifications.

## Tool retrieval (P3.3)

`agent/tool_retrieval.py: ToolSelector`. Behind the flag `PAP_TOOL_RETRIEVAL_RAGMCP` (default **off**, as in SPEC) and wired through `build_graph(tool_selector=...)`.
- **Index:** each tool's name, description and parameter names are embedded once.
- **Per model call:** the model is offered the top-k tools for the question, plus every tool already called in the run (a multi-step plan never loses its tool) and an always-include list.
- **Ordering:** selected tools keep catalog order, so the prompt prefix stays stable for caching.
- **Never widens access:** selection runs after the governance filter. An always-include of `create_ticket` is still invisible to a researcher (tested).

**Eval (`evals/tools/`).**
- Catalog: a deterministic synthetic set of up to 163 tools (20 domains × 8 verbs, plus our 3 real tools), with one gold tool per query.
- Measured: recall@k (the gold tool is offered), the size of the offered tool specs (an estimate: JSON chars / 4), and selection latency.
- Local run uses the hash embedder (lexical). CI's `eval-tools` workflow repeats it with fastembed bge-small (no API spend).
- **Not measured:** end-to-end task success with a real model (NOT MEASURED; it costs model calls).

**Result (hash embedder, local):** see RESULTS Phase 3C.
- With k=10, recall@k stays ≥ 0.97 up to 163 tools, and the offered specs shrink from 4,858 to about 295 tokens (−94%).
- With k=5, recall drops to 0.64 at 163 tools. Near-duplicate tools such as `list_invoice` and `list_invoice_payment` are where a lexical embedder fails.

**Default:** off.
- With few tools (we have 3–4 today), the selector returns them unchanged.
- Turn it on with k≈10 once a tenant has dozens of tools.
- Revisit k with the fastembed numbers from CI.
