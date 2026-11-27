# ADR 005: Compaction, experience memory, grounded reflection (Phase 2, P2.4-P2.6)

Status: accepted. Date: 2026-11-27.

All three are optional `build_graph` arguments and are off unless passed. ADR 004 still covers the loop.

## P2.4 Context compaction (`agent/compaction.py`)

- Each tool result is capped at `tool_result_chars` (default 2000) when it is recorded. The cap keeps the head (¾) and the tail (¼) with a marker in between.
- Before each model call, the graph estimates the size of the view at 4 chars per token (an ESTIMATE). Above `threshold_tokens` (default 6000), the observations older than the newest `keep_last` messages are folded into a running summary.
- That summary goes into the system message, and each old tool result becomes a stub. The cut never splits an assistant/tool group, so every `tool_use` still has its `tool_result`.
- The default summariser is extractive (the first line of each observation). It is free and deterministic. A model summariser fits the same `Summarizer` signature, and its tokens are charged to the run budget.
- The transcript in state stays append-only. Only the view sent to the model changes. Audit and replay therefore see everything, and the checkpoint reducer stays `operator.add`.
- Compacted assistant turns lose their raw provider blocks (including thinking) and are rebuilt from `tool_calls`. Whether Claude's preserved-thinking checks accept this mid-conversation is UNVERIFIED with thinking on. The eval runs Haiku with thinking disabled.
- Measured (scripted, 12 tool results of 6 kB each): peak view 17.8k → 4.7k estimated tokens, and the sum over the run 116k → 34k (RESULTS Phase 2B).

## P2.5 Experience memory (`agent/memory.py`)

ReasoningBank-style (Ouyang et al. 2025):

- After a run, the distiller is given the trajectory and an external outcome. In the eval, F1 ≥ 0.8 against gold is a success and F1 ≤ 0.2 is a failure. Runs in between are not distilled, because their label is mostly about answer phrasing (run 1). It writes 1-2 items: a strategy on success, a pitfall on failure.
- Items are per tenant, in `agent_memory` (Postgres + pgvector, exact scan filtered by tenant; `PgMemoryBank`). An in-memory bank with the same semantics serves tests and the eval.
- Recall: the top 2 items with cosine similarity ≥ 0.6 to the question are added to the system prompt as "Lessons from earlier tasks (may not apply)". Run 1 used top 3 at ≥ 0.3, which with bge-small injected lessons into nearly every question. 0.6 is not yet calibrated.

The admission gate checks cheap things first, so replays are only paid for items that could pass:

1. Length is 20-400 chars.
2. The item does not contain the source task's answer (a leak, not a lesson).
3. The item contains no secret-like `key=`/`password:` patterns.
4. It is not a near-duplicate (cosine ≥ 0.92) of an admitted item. This is checked again under a lock right before the insert, because replays run in parallel.
5. **Replay with/without (transfer test):** the most similar *other* question seen so far is re-run with only this item recalled, and compared with that question's own run without it. The item is admitted if the score goes up, or if an already solved question stays solved with no extra steps. A neutral item on a failed question is noise and is rejected. Run 1 replayed the source question instead. That admitted 54 items, and gated memory still lost 0.06 F1 to no memory, because a lesson that fits its own question need not transfer.

Ungated memory (the `on` arm) admits everything the distiller writes. It is the control that shows whether the gate earns its replay cost.

## P2.6 Grounded reflection (`agent/reflection.py`)

- When the model gives a final answer, verifiers check it against evidence outside the model:
  - `citations_grounded`: every `[doc_id]` cited came back from `search`/`fetch_doc` in this run, and at least one is cited.
  - `last_tools_failed`: the newest tool batch was not all errors.
- On a failure, the graph adds the failure reason as a user turn and loops back to `plan`. This happens at most `max_reflections` (default 1) times, and only while the budget allows.
- There is never a "check your work" prompt without a signal, since self-critique without external feedback tends to flip correct answers (Huang et al. 2024). The result is recorded as `verified` and `failure` in state.

## Telemetry

- `chat {model}` and `execute_tool {name}` spans carry `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens/output_tokens`, `gen_ai.tool.name/call.id`, and `pap.tenant_id`.
- No prompt, argument or output content is recorded (PII off, tested).
- **Semconv pin:** schema `https://opentelemetry.io/schemas/1.44.0`, attribute names from `opentelemetry-semantic-conventions==0.66b2` (GenAI attributes are Development status). The pin is by package version, not by repo commit: the semantic-conventions repository is not reachable from this sandbox (UNVERIFIED which commit 0.66b2 was cut from).

## Eval (`evals/agent/run.py`, workflow `eval-agent`)

- **Data:** HotpotQA distractor validation, the first 100 questions in dataset order. Their ~1000 paragraphs are indexed as one tenant with fastembed + hybrid search.
- **Model:** Claude Haiku 5.5 (thinking disabled, effort low).
- **Arms:** off / on / gated. All arms share search + fetch_doc, compaction and citation reflection, with a budget of 8 steps and $0.05 per question.
- **Order:** questions stream in batches of 10. Memory written after batch b is recalled from batch b+1 on.
- **Report:** EM, F1, F1 on the second half (where memory can matter), steps, and tokens. Each memory arm also gets ΔF1 vs off with a 95% paired-bootstrap CI.
- **Spend controls:** a hard cap of $2 on new API spend. Responses are cached by content in the Actions cache, so re-running unchanged code is free.
- **eval-gate:agent:** the job fails if gated F1 < off F1 − 0.05.
- Fork PRs without the secret report NOT MEASURED and pass.

## Result

- Run 1: memory hurt. Ungated was −0.091 F1, significant; gated was −0.062.
- Run 2, after the changes above: neither arm differs significantly from no memory (gated −0.034, CI [−0.082, +0.012]). The eval gate passes.
- Decision: memory is **off by default** (`recall=None`), and the code and gate stay. A tenant or workload turns it on only when this eval, run on its own data, shows a gain. See RESULTS Phase 2B.

## Rejected

- Rewriting state messages for compaction: this needs a custom reducer and loses the audit trail.
- LLM-as-judge for memory outcomes: it is not external. The grader (or, in production, user feedback or tests) is.
- Admitting on success label alone: a "success" can be lucky, and the replay catches items that do not transfer even to their own task.
- Reflection on every answer: it doubles cost and has no signal behind it.

## Open

- Production outcome signal: there is no feedback endpoint yet (Phase 5), so memory is written only by the eval.
- Memory decay or eviction, and per-item win-rate tracking.
- The replay cost of the gate grows with distilled items. It is bounded here by 2 items per run and the static checks.
