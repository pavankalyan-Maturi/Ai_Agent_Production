# Handoff

## Done
- Phases 0-5 merged (PR #1-#12). PR #13 merged: eval-agent gates only the shipped no-memory
  config against its recorded baseline (0.501 F1); memory arms report-only. (PR #13 left no
  handoff or COSTS row; both added here.)
- Phase 6 PR A (this PR, ADR 012):
  - **P6.1** `evals/trajectory.py`: tool calls, redundant calls, evidence recall vs HotpotQA
    supporting titles (`hotpot.py` now parses them; cache file v2), citation precision,
    grounded and degraded rates. `evals.agent.run` writes `trajectories.jsonl` for arm off.
  - **P6.2** `evals/judge.py`: cached Haiku CORRECT/INCORRECT judge, Cohen's kappa + bootstrap
    CI vs `evals/labels/hotpot_correctness.csv`, F1-rule kappa as the lexical baseline, label
    pool CSV export without scores.
  - **P6.3** rule-based MAST tags (FM-1.1, 1.3, 1.5, 3.1, 3.2, 3.3) + histogram.
  - **P6.4** `evals/gate.py` + `evals/gate.toml` + `eval-gate.yml`: paired bootstrap per
    question, per-metric enforcement, $0.30 cost cap. eval-agent (memory ablation) is now manual.

## Verified here
- Gate: ruff, format, mypy, 165 unit tests (7 new in tests/evals_gate).
- `python -m evals.gate` end to end on a synthetic trajectory file (no model).

## Not verified
- CI eval-gate on PR #14: F1 0.501 reproduced from cache ($0.00), judge $0.003; RESULTS.md Phase 6A.
- Judge kappa 0.827 [0.657, 0.96] vs 100 human labels (F1 rule 0.241). MAST rule tags not checked against human tags.

## Open
- Labels in (PR #16) and per-question baseline committed (PR #17). Thresholds signed off: f1,
  judge_correct and cost cap enforced; trajectory metrics and $/q report-only.
- P6.5 GEPA vs MIPROv2: CUT by user decision (cut list #2), recorded in ADR 012.
- Gate covers the agent eval only; retrieval/security keep their own workflow rules.
- From before: agent graph does not use gateway cache/limiter; Keycloak compose profile.

## Next step
Phase 6 done once PR #17 merges. Next: Phase 7 (vLLM, human-run) or the deferred Phase 4 follow-ups, in a fresh thread.

## Versions pinned
No new pins (tomllib is stdlib). docs/SPEC.md Pins table, ADR 002-012.
