# Handoff

## Done
- Phases 0-3 merged (PR #1-#8). Phase 5 PR A merged (PR #9, ADR 009). Phase 4 (gateway) not built.
- Phase 5 PR B (this PR, not merged), ADR 010:
  - **P5.3** `guardrails/taint.py`: taint ledger rebuilt from the transcript; plan/data separation
    (`Guardrails(quarantine=model)`): data-tool output becomes `$vN`, the planner sees handles,
    `extract` runs a tool-less reader with typed validation (number/date/name/text); variables are
    filled into tool args and the answer after the plan is fixed.
  - **P5.4** `guardrails/policy.py`: step rules (taint_to_sink, tainted_var_to_sink) and trace rules
    (sink_without_intent, sink_budget, denial_limit), checked in `plan` before the approval pause;
    denials audited through mcp-rbac-gateway's audit log (`approver=policy:<rule>`).
  - Suite: +10 benign_action cases (70 total); arms `policy` and `camel`; scripted worst-case run.
  - PR A's Haiku ASR recorded in RESULTS (38% / 25% / 18%).

## Verified here
- Gate: ruff, format, mypy, 145 unit tests (7 new).
- Scripted worst case: policy exfil ASR 100% -> 0%, answer ASR unchanged (100%); camel 0% ASR;
  0/30 false blocks; policy check p50 0.014 ms.

## Not verified
- Haiku 5.5 on the `policy` and `camel` arms (ASR, benign accuracy, false blocks): CI
  `eval-security` job summary, cap $1 (expected ~$0.1-0.2).

## Open
- Move sink definitions into sentinel.yaml when the library has a place for them.
- Keycloak compose profile; approver from token + tenant-filtered approvals; merge JwtVerifiers.
- Phase 4 gateway. HotpotQA memory gate red on main (separate task).

## Next step
Review PR B and the eval-security numbers. Then Phase 4 or the memory-gate fix, in a fresh thread.

## Versions pinned
docs/SPEC.md Pins table, ADR 002-010 (no new dependencies in PR B).
