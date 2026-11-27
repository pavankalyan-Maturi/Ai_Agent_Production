#!/usr/bin/env bash
# Start vLLM on the GPU box in one of four benchmark arms (ADR 013, docs/runbooks/vllm.md).
#   serving/serve.sh <baseline|prefix|eagle3|prefix+eagle3>
# Needs: Docker + NVIDIA container toolkit, HF_TOKEN (Llama is gated), VLLM_API_KEY.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=/dev/null
source ./vllm.env
ARM="${1:?usage: serve.sh baseline|prefix|eagle3|prefix+eagle3}"
: "${HF_TOKEN:?export HF_TOKEN}" "${VLLM_API_KEY:?export VLLM_API_KEY}"

args=(--model "$MODEL" --served-model-name "$SERVED_NAME" --max-model-len "$MAX_MODEL_LEN"
      --gpu-memory-utilization "$GPU_MEM_UTIL" --max-num-seqs "$MAX_NUM_SEQS"
      --api-key "$VLLM_API_KEY" --port "$PORT" --disable-log-requests)
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

docker rm -f pap-vllm >/dev/null 2>&1 || true
exec docker run --name pap-vllm --gpus all --ipc=host -p "127.0.0.1:${PORT}:${PORT}" \
  -e HF_TOKEN -v "${HF_HOME:-$HOME/.cache/huggingface}:/root/.cache/huggingface" \
  "$VLLM_IMAGE" "${args[@]}"
