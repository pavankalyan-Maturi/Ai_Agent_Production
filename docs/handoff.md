# Handoff

## Latest: P7.2 runbook fix (before the first GPU run)
- `serving/serve.sh`: dropped `--disable-log-requests` (removed in vLLM 0.34, checked against
  v0.34.0 `arg_utils.py`; every arm would have failed at startup). Runs `vllm serve` natively when
  there is no Docker (RunPod pods are containers), `VLLM_RUNNER=docker|native` to force.
- `serving/run_all.sh`: the four-arm loop as one script; a dead or slow-to-start arm is written as
  `did_not_run` instead of hanging the old `until curl` loop. Runbook step 1-3 updated.
- Verified here: gate green, 180 passed (2 new tests with a fake `vllm`). Happy path of
  `run_all.sh` against a real server: UNVERIFIED (needs the GPU).
- Next: user runs docs/runbooks/vllm.md on an L40S (P7.3).

## Done
- Phases 0-6 and Phase 4 follow-ups merged (PR #1-#18).
- Phase 7 P7.1 + P7.2 (this PR):
  - `serving/vllm.env` + `serving/serve.sh`: vLLM 0.34.0 docker launch, 4 arms
    (baseline / prefix / eagle3 / prefix+eagle3), Llama-3.1-8B-Instruct served as `local-llm`.
  - `serving/bench.py`: streaming TTFT p50/p95, TPOT p50, output tok/s at concurrency 1/8/32,
    shared ~1.8k-token system prompt; JSON out. Works direct or through LiteLLM.
  - `gateway/litellm.yaml`: `local-vllm` -> `hosted_vllm/local-llm`, env base/key, fallback to
    `agent-fast`. Compose defaults `VLLM_API_BASE` to the mock; compose smoke calls `local-vllm`.
  - `docs/runbooks/vllm.md` (rent, run, collect, teardown, cost estimate), ADR 013.
  - `serving` added to mypy in the gate (CLAUDE.md, ci.yml) and to wheel packages.

## Verified here
- Gate green: ruff, format, mypy (incl. serving), pytest 178 passed (5 new in tests/serving:
  bench against the mock, error counting, TPOT math, LiteLLM entry, SPEC pin).
- `docker compose --profile core config`, `bash -n serving/serve.sh`.

## Not verified
- Anything on a GPU: vLLM 0.34 flags, EAGLE-3 draft head compatibility, all numbers. NOT MEASURED.
- LiteLLM `hosted_vllm` route: only the compose-smoke CI step exercises it (litellm not installed
  in the dev env).
- RunPod prices in the runbook are estimates.

## Open
- P7.3 (human): follow docs/runbooks/vllm.md, commit serving/results, fill RESULTS.md table.
- Carried over: evals use AnthropicModel directly (opt-in GatewayModel); approvals API approver
  from body + untenanted pending approvals (ADR 009, library <0.3); gate covers agent eval only.

## Next step
User runs P7.3 on a rented GPU. Phase 8 only when the user says so.

## Versions pinned
vLLM 0.34.0 image `vllm/vllm-openai:v0.34.0`, model Llama-3.1-8B-Instruct, draft
`yuhuili/EAGLE3-LLaMA3.1-Instruct-8B` (ADR 013, SPEC row). Others unchanged.
