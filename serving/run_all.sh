#!/usr/bin/env bash
# Run the four benchmark arms one after another (docs/runbooks/vllm.md step 3).
#   serving/run_all.sh [arm ...]     default: baseline prefix eagle3 prefix+eagle3
# An arm whose server dies or is not up within STARTUP_TIMEOUT_S is logged as "did not run"
# and skipped, instead of hanging the loop.
set -uo pipefail
cd "$(dirname "$0")/.."
: "${VLLM_API_KEY:?export VLLM_API_KEY}"
STARTUP_TIMEOUT_S="${STARTUP_TIMEOUT_S:-1200}"   # first arm downloads ~16 GB of weights
mkdir -p serving/results
arms=("$@"); [[ ${#arms[@]} -gt 0 ]] || arms=(baseline prefix eagle3 prefix+eagle3)

for arm in "${arms[@]}"; do
  echo "== $arm: starting server (log: serving/results/$arm.log)"
  serving/serve.sh "$arm" > "serving/results/$arm.log" 2>&1 &
  pid=$!
  up=0
  for ((t = 0; t < STARTUP_TIMEOUT_S; t += 5)); do
    if curl -fsS -H "Authorization: Bearer $VLLM_API_KEY" localhost:8000/v1/models >/dev/null 2>&1; then
      up=1; break
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 5
  done
  if [[ $up == 1 ]]; then
    echo "== $arm: server up after ~${t}s, benchmarking"
    uv run python -m serving.bench --arm "$arm" --concurrency 1 8 32 --requests 64 \
      --out "serving/results/$arm.json" > /dev/null && echo "== $arm: done"
  else
    echo "== $arm: DID NOT RUN (see tail of serving/results/$arm.log)"
    echo "{\"arm\": \"$arm\", \"did_not_run\": true}" > "serving/results/$arm.json"
  fi
  kill "$pid" 2>/dev/null; docker rm -f pap-vllm >/dev/null 2>&1; wait "$pid" 2>/dev/null
  sleep "${ARM_GAP_S:-10}"   # let the GPU memory free before the next arm
done
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv > serving/results/gpu.csv
echo "== all arms finished; results in serving/results/"
