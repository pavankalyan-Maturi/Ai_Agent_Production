# ADR 012: Eval gate: per-question paired bootstrap on the shipped config, judge gated by kappa (Phase 6)

Status: accepted. Thresholds signed off 2026-11-27: f1, judge_correct (kappa 0.827) and cost cap enforced.

## Context
Until now each eval workflow carried its own pass/fail rule; eval-agent compared one mean F1
against a recorded scalar (0.501 ± 0.05). That rule cannot tell noise from a regression, ignores
how the agent got its answer, and HotpotQA token F1 mis-grades paraphrases. Phase 6 needs one
gate that blocks a PR on a regression beyond its confidence bound, stays inside a cost cap, and
only trusts an LLM judge after it agrees with a human.

## Decision
1. **One gate workflow** (`eval-gate.yml`) on PRs touching `agent/`, `evals/`, `retrieval/`, and
   on main pushes (seeds the shared `agent-llm-` response cache). It runs only the shipped config
   (memory off, ADR 005); the memory ablation (`eval-agent.yml`) becomes manual. Net effect: one
   Haiku run per PR instead of three arms.
2. **Paired bootstrap per question** (`evals/gate.py`) against `evals/baselines/agent_off.json`.
   A "higher is better" metric fails when the 95% CI upper bound of (PR − base) is below 0 and
   the mean drop is at least `min_effect`; "lower" mirrors it. Responses are content-addressed,
   so an unchanged prompt replays the baseline answer exactly and Δ is 0, not noise.
   Until the per-question baseline is committed, F1 falls back to the old scalar rule.
3. **Trajectory metrics** (`evals/trajectory.py`), deterministic and free: tool calls, redundant
   calls (same tool + args), evidence recall (gold supporting titles the agent saw), citation
   precision, grounded rate, degraded rate.
4. **MAST tags, rule-based.** 6 of the 14 MAST modes are decidable from a single-agent trace
   (FM-1.1, 1.3, 1.5, 3.1, 3.2, 3.3); inter-agent modes (FM-2.x) do not apply to one planner.
   Multi-label histogram over failed runs. Report-only.
5. **Judge** (`evals/judge.py`): Haiku 5.5, one word CORRECT/INCORRECT, citations stripped,
   responses cached. `judge_correct` may gate only when Cohen's kappa vs ≥100 human labels in
   `evals/labels/hotpot_correctness.csv` is ≥ 0.6. The F1 ≥ 0.5 rule is scored against the same
   labels, so the judge has to beat the lexical baseline to be worth its cost.
6. **Cost cap**: new spend of agent run + judge ≤ $0.30 per run, enforced (agent $0.25, judge $0.05; the user's API balance is low).
7. **Enforcement** per metric in `gate.toml`: F1, judge accuracy and the cost cap block a PR;
   evidence recall, redundant calls and $/question are report-only (no history yet).

## Alternatives
- *Fixed tolerance on the mean* (old rule): no notion of variance; with n=100 a ±0.05 band is
  both too loose for real drops and flaky across re-samples (PR #8).
- *Unpaired bootstrap or t-test against a stored mean*: wastes the pairing; per-question deltas
  have far lower variance because question difficulty cancels.
- *LLM-annotated MAST (paper protocol)*: one more model call per failure plus its own kappa
  study; deferred until rule tags are checked against human tags.
- *RAGAS / DeepEval judges*: extra dependency and prompts we would still need to calibrate.
- *Judge with a bigger model*: costs more; only worth it if Haiku's kappa is below 0.6.

## Tradeoffs
- The gate covers the agent eval only; retrieval and security evals keep their own workflow
  rules for now.
- A real prompt change re-samples every call: Δ then includes sampling noise, which the CI bound
  absorbs at the cost of missing drops smaller than ~0.03–0.05 F1 at n=100.
- Rule-based MAST tags are an approximation and UNVERIFIED against human tags.

## Pins
No new dependency (`tomllib`, stdlib). Judge model claude-haiku-5-5 (as ADR 005).

## Cut
P6.5 GEPA vs MIPROv2 is cut (user decision 2026-11-27, SPEC cut list #2): credit is better
spent on the gate, which the cut list says is never cut. NOT MEASURED, not attempted.
