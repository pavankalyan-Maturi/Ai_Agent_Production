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
    question, per-metric enforcement, $1 cost cap. eval-agent (memory ablation) is now manual.

## Verified here
- Gate: ruff, format, mypy, 165 unit tests (7 new in tests/evals_gate).
- `python -m evals.gate` end to end on a synthetic trajectory file (no model).

## Not verified
- Real numbers come from CI eval-gate (Haiku); see RESULTS.md Phase 6 once it has run.
- Judge kappa: BLOCKED on human labels. MAST rule tags not checked against human tags.

## Open
- CHECKPOINT: user labels 100 items and signs off thresholds in `evals/gate.toml`; then commit
  `evals/baselines/agent_off.json` (from the run's `baseline_candidate.json`) and flip enforce.
- P6.5 GEPA vs MIPROv2 (cut list #2): not started, awaiting a go/cut decision.
- Gate covers the agent eval only; retrieval/security keep their own workflow rules.
- From before: agent graph does not use gateway cache/limiter; Keycloak compose profile.

## Next step
Label `results/gate/label_pool.csv` from the eval-gate artifact, sign off thresholds.

## Versions pinned
No new pins (tomllib is stdlib). docs/SPEC.md Pins table, ADR 002-012.
