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
- RunPod, template "RunPod PyTorch" (any CUDA 12.x image with Docker) or a bare GPU VM with the
  NVIDIA container toolkit. 1 GPU, 60 GB disk. Expose no public port; use SSH port forwarding.
- On the box: `nvidia-smi` shows the GPU; `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` works.
  (If the template has no Docker, run `pip install vllm==0.34.0` and replace `docker run ... $VLLM_IMAGE` in
  `serving/serve.sh` with `vllm serve` and the same args.)

## 2. Setup
```bash
git clone https://github.com/pavankalyan-Maturi/Ai_Agent_Production && cd Ai_Agent_Production
pip install uv && uv sync --frozen
export HF_TOKEN=...            # accept the Llama 3.1 licence on HF first
export VLLM_API_KEY=$(openssl rand -hex 16)
mkdir -p serving/results
```

## 3. Run the four arms (one server at a time)
```bash
for arm in baseline prefix eagle3 prefix+eagle3; do
  serving/serve.sh "$arm" > "serving/results/$arm.log" 2>&1 &
  until curl -fsS -H "Authorization: Bearer $VLLM_API_KEY" localhost:8000/v1/models >/dev/null; do sleep 5; done
  uv run python -m serving.bench --arm "$arm" --concurrency 1 8 32 --requests 64 \
    --out "serving/results/$arm.json"
  docker rm -f pap-vllm; wait
done
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv > serving/results/gpu.csv
```
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
