# Runbook: vLLM on a rented GPU (Phase 7, P7.2 / P7.3)

Human-run. The cloud thread has no GPU; everything below is UNVERIFIED until you run it.
Decision record: ADR 013. Pins: `serving/vllm.env`.

## 0. Cost (ESTIMATE, check the live price before renting)
| GPU | RunPod on-demand (approx.) | Time for this runbook | Est. cost |
|---|---|---|---|
| L40S 48 GB (default) | ~$0.9/h | ~1.5 h (pull + download + 4 arms) | ~$1.5 |
| H100 80 GB | ~$2.5-3/h | ~1.2 h | ~$3.5 |
Stop the pod as soon as step 5 is done. A stopped pod with a volume still bills storage; terminate it.

## 1. Rent
- RunPod pod, template "RunPod PyTorch" (CUDA 12.x), 1x L40S, 80 GB container disk, no network volume (nothing to pay for after terminate).
  Expose no public HTTP port; connect with the pod's SSH command. RunPod pods are containers and
  have **no Docker**, so `serving/serve.sh` runs `vllm serve` natively (auto-detected).
- Or a bare GPU VM with Docker + the NVIDIA container toolkit; then `serve.sh` uses `$VLLM_IMAGE`.
- On the box: `nvidia-smi` shows the GPU.

## 2. Setup (everything inside tmux, so a dropped SSH connection does not kill the run)
```bash
tmux new -s bench              # reattach later with: tmux attach -t bench
cd /root && git clone https://github.com/pavankalyan-Maturi/Ai_Agent_Production && cd Ai_Agent_Production
pip install uv && uv sync --frozen
# No Docker (RunPod): vLLM in its own venv, not .venv, so `uv run` never re-syncs it away.
uv venv /opt/vllm --python 3.12 && uv pip install --python /opt/vllm vllm==0.34.0
export HF_TOKEN=...            # accept the Llama 3.1 licence on HF first; export INSIDE tmux
export VLLM_API_KEY=$(openssl rand -hex 16)
```

## 3. Run the four arms (one server at a time)
```bash
serving/run_all.sh             # ~40-60 min; first arm downloads the weights
```
It starts each arm, waits for `/v1/models`, runs `serving.bench` at concurrency 1/8/32 x 64
requests, stops the server and writes `serving/results/<arm>.json`, `<arm>.log` and `gpu.csv`.
An arm whose server dies or does not come up in 20 min is written as `{"did_not_run": true}`.
If `eagle3` fails to start (flag or draft-head mismatch on 0.34), keep the log, skip that arm and
record it as "did not run" in RESULTS. Do not swap the draft model silently.

## 4. Optional: through the gateway
From your laptop: `ssh -L 8000:127.0.0.1:8000 <pod>`, then
`VLLM_API_BASE=http://host.docker.internal:8000/v1 VLLM_API_KEY=... docker compose ... up` and call
model `local-vllm` via LiteLLM. Bench through LiteLLM with
`uv run python -m serving.bench --base-url http://localhost:4000/v1 --model local-vllm --api-key-env LITELLM_MASTER_KEY`
to see the proxy's overhead.

## 5. Collect and tear down
```bash
tar czf vllm-results.tgz serving/results     # copy off the box (scp) before terminating
```
Terminate the pod (not just stop). Check the billing page shows it gone.
Commit `serving/results/*.json`, `gpu.csv` and the tail of each log on a P7.3 branch, and add the
TTFT/TPOT/tok-s table to `docs/RESULTS.md` (arm x concurrency), including any arm that did not run.

## What to look for
- prefix vs baseline: TTFT should drop at every concurrency (shared ~1.8k-token system prompt).
- eagle3 vs baseline: TPOT should drop at batch 1; the gain usually shrinks or reverses at 32
  because the GPU is already compute-bound. Report it either way.
