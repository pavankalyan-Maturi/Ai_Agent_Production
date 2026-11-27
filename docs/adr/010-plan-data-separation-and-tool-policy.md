# ADR 010: Plan/data separation with taint, and a step + trace tool-call policy (Phase 5 PR B)

Status: proposed (PR B of Phase 5). Builds on ADR 006 (mcp-rbac-gateway) and ADR 009.

## Context
PR A's Haiku run: attack success (ASR) none 38%, scanner 25%, scanner+spotlight 18%.
Paraphrase (60%) and authority-style (40%) attacks still leaked under spotlighting. The gateway
judges each call alone and keyword heuristics miss these families, so the next layer has to be
structural: it must not depend on recognising the attack text.

## Decision
1. **Taint ledger** (`guardrails/taint.py`), rebuilt from the transcript at each step (no new
   mutable state to checkpoint or corrupt). Trusted = system and user turns. Untrusted = output
   of `untrusted_tools` (search, fetch_doc) and every value extracted from it. A token is tainted
   when it appears in untrusted text and nowhere in trusted text.
2. **P5.4 tool-call policy** (`guardrails/policy.py`), evaluated in `plan` right after the model
   decides, so a refused call never reaches the approval pause or the gateway. Rules:
   - step: `taint_to_sink` (a sink's control field, e.g. ticket title, carries tainted tokens),
     `tainted_var_to_sink` (a control field references a tainted variable);
   - trace: `sink_without_intent` (a sink after untrusted data entered the run, and the user's
     request never asked for that action), `sink_budget` (1 sink call per run), `denial_limit`
     (2 denials end the run with a fixed answer, not the degrade path, which would echo evidence).
   Reads are never blocked. Sink data fields (ticket body) may carry untrusted values, but only
   when the user asked for the sink. Denials are audited in the library's audit log via
   `Governance.record_rejection(approver="policy:<rule>")`, so one audit trail covers both.
   Events keep the rule and tool, never argument values.
3. **P5.3 plan/data separation (CaMeL-lite)**, on when `Guardrails.quarantine` is set. Data-tool
   output is stored as `$vN` in state; the planner sees only a handle. It calls `extract(var,
   question, type)`, which runs a tool-less quarantined model over the spotlighted data. The reply
   must validate as `number`, `date`, `name` or `text`, and becomes a new tainted variable the
   planner also cannot read. Variables are substituted into tool args and the final answer after
   the planner decided. Injected text can change a value, never the plan.

## Alternatives
- **Full CaMeL** (P-LLM writes Python, a custom interpreter tracks capabilities per value). It is
  the stronger guarantee, at the cost of an interpreter and a new failure surface. The lite
  version keeps the LangGraph loop and gets the same separation for this tool set.
- **An LLM judge on each tool call** (e.g. a second model asks "is this call user-intended?").
  It is injectable itself and adds a model call per step. The rules here cost ~0.01 ms.
- **Intent from a classifier** instead of keywords. `sink_without_intent` uses words in the
  request ("ticket"). It is crude and can be fooled by a user who mentions tickets in passing,
  but it reads only trusted text, so an attacker cannot move it.
- **Block all tainted data in sinks.** Simple and safe, but a user who asks "file a ticket with
  the answer" would always be refused. Hence control vs data fields.

## Measured (this sandbox, `uv run python -m evals.security.run`, no spend)
Scripted worst case: the planner obeys every injection it can read, or (camel arm) the
quarantined reader obeys the document and the planner follows the user.

| arm | ASR | exfil ASR | answer ASR | benign acc | ticket asks done | false blocks |
|---|---|---|---|---|---|---|
| none | 100% | 100% | 100% | 100% | 100% | 0% |
| policy | 88% | 0% | 100% | 100% | 100% | 0% of 30 |
| camel | 0% | 0% | 0% | 100% | 100% | 0% of 30 |

Policy check p50 0.014 ms / p95 0.025 ms per step (n=300). The policy alone stops only the
tool-exfil family: it does nothing for attacks that target the answer text. The camel arm's 0%
on answers comes from typed validation (`1931 ZQX-...` is not a `date`).

Haiku 5.5 per arm (including `policy` and `camel`, false blocks, benign utility): CI
`eval-security`. NOT MEASURED here (no API access).

## Consequences and limits
- The `text` type accepts any short line, so an obedient reader can still poison a text answer;
  only number/date/name are tight. Poisoning a *value* (a wrong but valid date) is outside what
  any of this can detect; CaMeL has the same limit.
- In camel mode the planner cannot read snippets, so it cannot pick doc ids or cite. Citations
  and multi-hop reading are the utility cost; the Haiku run measures how much.
- Taint is token-level: a sink title paraphrasing untrusted text in new words is not tainted.
- Policy is code (`Policy()` defaults), not yet in sentinel.yaml; moving sinks to the library's
  config is a follow-up once mcp-rbac-gateway has a place for it.

## Pins
No new dependencies. mcp-rbac-gateway 0.2.0, LangGraph and the rest as in docs/SPEC.md.
