# Handoff

## Latest: runbook fixes from the first real RunPod run (2026-11-28)
- The user hit two gaps on the L40S: exports made before `tmux new` are lost inside tmux, and
  `pip install vllm` was not on PATH after `uv sync`, so all four arms "did not run".
- Runbook step 2 now starts tmux first and exports inside it; vLLM goes into its own venv
  (`/opt/vllm`) so `uv run` never re-syncs it. `serve.sh` finds `/opt/vllm/bin/vllm` (or
  `VLLM_BIN`); `run_all.sh` checks HF_TOKEN + vllm up front, prints a dead arm's log tail,
  and runs the bench with `uv run --no-sync`.
- Verified here: gate green, 189 passed (1 new preflight test). GPU path UNVERIFIED by me;
  the user's run is in progress.

## Latest: Phase 8 (P8.1-P8.3 code) AWS burst as code + runbook, nothing applied
- `deploy/terraform/eks`: VPC (public subnets, no NAT), EKS 1.34 standard support, spot node group
  (1 AZ), EBS CSI + core add-ons, IRSA (EBS CSI, LBC, pgBackRest), S3 pgBackRest repo, ECR,
  Secrets Manager container, AWS Budget. `deploy/terraform/k3s`: one spot EC2 k3s node, 10 h
  self-terminate.
- `deploy/helm/pap`: api (probes, PDB, non-root), mock LLM, Redis, KEDA ScaledObject (CPU),
  ALB Ingress (EKS values), Crunchy PGO PostgresCluster (pgvector, PgBouncer, pgBackRest on S3).
- `deploy/argocd/application.yaml`, `deploy/chaos/pod-kill.yaml`, `deploy/pgbackrest/`
  (restore cluster + `restore_drill.sh` writing RTO JSON).
- `scripts/burst_up.sh`, `scripts/burst_destroy.sh` (ALB/EBS first, terraform destroy, leftover and
  bill check), `scripts/check_manifests.sh`; CI `infra.yml` (fmt, validate, tflint, helm lint,
  kubeconform). Runbook `docs/runbooks/aws-burst.md` with cost estimate and cost-guard checklist.
  ADR 014, SPEC pin rows.
- Verified here: terraform fmt/validate + tflint clean (both modules), helm lint/template,
  kubeconform -strict 26/26 resources, bash -n, gate green (see PR). NOT run: anything on AWS.
- Next: when there is budget, user runs docs/runbooks/aws-burst.md and pastes evidence (P8.3).

## Previous: P7.2 runbook fix (before the first GPU run)
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
- P8.3 (human): AWS burst run + evidence; needs ~$2-5 of AWS.
- P7.3 (human): follow docs/runbooks/vllm.md, commit serving/results, fill RESULTS.md table.
- Carried over: evals use AnthropicModel directly (opt-in GatewayModel); approvals API approver
  from body + untenanted pending approvals (ADR 009, library <0.3); gate covers agent eval only.

## Next step
Phases 1-8 have code. Human-run items left: P7.3 (GPU) and P8.3 (AWS), both waiting on budget.

## Versions pinned
Phase 8 (ADR 014): Terraform 1.13.4, aws provider 6.20.0, EKS 1.34, k3s v1.34.1+k3s1, LBC 3.2.0,
metrics-server 3.13.0, KEDA 2.18.1, Argo CD chart 9.1.0, Chaos Mesh 2.7.3, PGO 5.8.2 (UNVERIFIED).
vLLM 0.34.0 image `vllm/vllm-openai:v0.34.0`, model Llama-3.1-8B-Instruct, draft
`yuhuili/EAGLE3-LLaMA3.1-Instruct-8B` (ADR 013, SPEC row). Others unchanged.
