# ADR 004: Agent graph, checkpointing, budgets, HITL (Phase 2, P2.1-P2.3)

Status: accepted. Date: 2026-11-26.

## Decision

`agent/` is a LangGraph `StateGraph` with five nodes:

```
START -> start -> plan --(final answer)--------------------> END
                  plan --(tool calls, none flagged)--> act --> plan
                  plan --(a flagged tool)--> approve --> act --> plan
                  plan --(budget exhausted)--> degrade --> END
```

- **Model interface.** `ChatModel.decide(messages, tools) -> Decision` (text, tool calls, token usage, raw provider blocks). CI uses `ScriptedModel` (a deterministic policy, no network, $0). `AnthropicModel` (default `claude-sonnet-5-5`, client-side tools, raw assistant blocks replayed unchanged so thinking blocks survive) is wired but UNVERIFIED: no live call in this PR. Routing through the LiteLLM proxy comes with Phase 4; the Protocol is the seam.
- **Tools.** `search` (hybrid retrieval, ADR 002), `fetch_doc` (reassembles a document from its chunks), `calculator` (AST walk: numbers and `+ - * / ** %` only, exponent ≤ 100, 200 chars). Each tool is `fn(tenant_id, args)`, and `tenant_id` comes from graph state, which the caller sets from the authenticated request. A model that puts `tenant_id` in its arguments changes nothing (tested). Tool exceptions and unknown tool names become observations, not crashes.
- **Checkpointer.** `AsyncPostgresSaver` (langgraph-checkpoint-postgres 3.2.1) in the main Postgres. `thread_id = "{tenant}:{run_id}"`. `setup()` runs behind a session advisory lock, because concurrent workers racing on `CREATE ... IF NOT EXISTS` is the same failure that lost a message in Phase 1B. Resume = `ainvoke(None, config)` from the last checkpoint.
- **Delivery semantics.** A checkpoint is written after each super-step. A worker killed inside `act` re-runs that step's tool calls on resume (at-least-once). Completed steps never re-run (tested). Side-effecting tools should be idempotent or flagged.
- **Budgets.** `Budget(max_steps=8, max_tokens=50_000, max_usd=0.25)`, checked before every model call against the totals in state. USD comes from usage × list price (Sonnet 5.5 $2/$10 per MTok, Haiku 5.5 $0.10/$0.50). A run can overshoot by at most one call. Prompt-cache discounts are not counted, so the estimate is conservative. On exhaustion, `degrade` returns a marked partial answer built from the last three observations without another model call (`status=degraded`, `stop_reason=max_steps|max_tokens|max_usd`).
- **HITL.** Tools with `requires_approval=True` route to `approve`, which calls `interrupt()` first, before any side effect, since the node re-runs from the top on resume. The caller resumes with `Command(resume={"approved": bool})`. A rejection answers every pending call with `"rejected by human reviewer"`, so the transcript stays valid. The pause is a checkpoint, so approval can come from another process (tested with two separate savers).

## Measured

See RESULTS, Phase 2A. Locally, with a scripted model, 10 tool steps and 22 super-steps per run: Postgres checkpointing adds ≈2.2 ms per super-step (p50 run 67.8 ms vs 19.8 ms in memory). SIGKILL mid-tool, then resume: 10/10 runs finished correctly, resume p50 23 ms.

## Rejected

- `langgraph-prebuilt` `create_react_agent`: hides the routing we need for budgets and approval. It stays pinned (SPEC) but unused.
- Budget enforced inside the model client: the graph would not know why it stopped, and could not degrade.
- Interrupting inside `act` after some calls ran: on resume the node re-runs from the top and repeats them.
- `MemorySaver` in production: loses runs on restart.

## Pins

langgraph 1.2.24, langgraph-checkpoint 4.3.2, langgraph-checkpoint-postgres 3.2.1, langgraph-prebuilt 1.1.3 (installed, `uv.lock`). OTel GenAI semconv: no agent spans yet. Pinned by commit in P2 PR B, when spans land. UNVERIFIED until then.

## Open

- PgBouncer: `from_conn_string` uses `prepare_threshold=0`. Behaviour behind transaction pooling is UNVERIFIED. Tests connect to Postgres directly.
- Checkpoint retention: no pruning yet. Rows grow with every run.
- No HTTP endpoint for runs or approvals yet (Phase 5 auth).
