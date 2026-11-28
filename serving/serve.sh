#!/usr/bin/env bash
# Start vLLM on the GPU box in one of four benchmark arms (ADR 013, docs/runbooks/vllm.md).
#   serving/serve.sh <baseline|prefix|eagle3|prefix+eagle3>
# Needs: HF_TOKEN (Llama is gated), VLLM_API_KEY, and either Docker + NVIDIA container toolkit
# (bare VM) or `pip install vllm==0.34.0` (RunPod and other container hosts have no Docker).
# VLLM_RUNNER=docker|native forces one; default is docker if present, else native.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=/dev/null
source ./vllm.env
ARM="${1:?usage: serve.sh baseline|prefix|eagle3|prefix+eagle3}"
: "${HF_TOKEN:?export HF_TOKEN}" "${VLLM_API_KEY:?export VLLM_API_KEY}"

args=(--model "$MODEL" --served-model-name "$SERVED_NAME" --max-model-len "$MAX_MODEL_LEN"
      --gpu-memory-utilization "$GPU_MEM_UTIL" --max-num-seqs "$MAX_NUM_SEQS"
      --api-key "$VLLM_API_KEY" --port "$PORT")
# Request logging is off by default in 0.34 (--disable-log-requests was removed).
case "$ARM" in
  baseline)      args+=(--no-enable-prefix-caching) ;;
  prefix)        args+=(--enable-prefix-caching) ;;
  eagle3)        args+=(--no-enable-prefix-caching) ;;
  prefix+eagle3) args+=(--enable-prefix-caching) ;;
  *) echo "unknown arm: $ARM" >&2; exit 2 ;;
esac
if [[ "$ARM" == *eagle3 ]]; then
  args+=(--speculative-config
         "{\"method\":\"eagle3\",\"model\":\"$EAGLE3_DRAFT\",\"num_speculative_tokens\":$NUM_SPEC_TOKENS}")
fi

RUNNER="${VLLM_RUNNER:-$(command -v docker >/dev/null && echo docker || echo native)}"
if [[ "$RUNNER" == native ]]; then
  # vLLM lives in its own venv (runbook step 2) so it never fights the project lockfile.
  VLLM_BIN="${VLLM_BIN:-$(command -v vllm || echo /opt/vllm/bin/vllm)}"
  [[ -x "$VLLM_BIN" ]] || { echo "vllm not found: install it (runbook step 2) or set VLLM_BIN" >&2; exit 127; }
  exec "$VLLM_BIN" serve "${args[@]}"
fi
docker rm -f pap-vllm >/dev/null 2>&1 || true
exec docker run --name pap-vllm --gpus all --ipc=host -p "127.0.0.1:${PORT}:${PORT}" \
  -e HF_TOKEN -v "${HF_HOME:-$HOME/.cache/huggingface}:/root/.cache/huggingface" \
  "$VLLM_IMAGE" "${args[@]}"
