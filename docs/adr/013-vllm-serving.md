# ADR 013: vLLM serving behind LiteLLM (Phase 7)

Status: accepted (config + bench + runbook). GPU numbers: NOT MEASURED (human-run, P7.3).

## Decision
- vLLM **0.34.0** (`vllm/vllm-openai:v0.34.0`, SPEC pin), OpenAI-compatible server, one GPU.
- Model **meta-llama/Llama-3.1-8B-Instruct**, served as `local-llm`. Chosen because it fits an L40S
  with room for KV cache at 8k context and has a published EAGLE-3 draft head
  (`yuhuili/EAGLE3-LLaMA3.1-Instruct-8B`). Draft/vLLM-version compatibility is UNVERIFIED.
- LiteLLM entry `local-vllm` -> `hosted_vllm/local-llm`, `api_base`/`api_key` from env. The served
  name decouples the gateway from the HF repo, so swapping the model is a `vllm.env` change only.
- Fallback `local-vllm -> agent-fast` (Haiku). Availability over cost; delete the line to fail closed.
- Compose defaults `VLLM_API_BASE` to the mock, so CI's compose smoke exercises the real
  `hosted_vllm` provider path in LiteLLM 1.110.0 without a GPU.
- Benchmark: 2x2 arms (prefix caching on/off x EAGLE-3 on/off) at concurrency 1/8/32, workload with
  a shared ~1.8k-token system prompt (the agent's real shape). Metrics: TTFT p50/p95, TPOT p50,
  output tok/s. `serving/bench.py` works against vLLM directly or through LiteLLM.

## Alternatives
- **SGLang**: comparable throughput, RadixAttention prefix reuse; rejected to keep the SPEC pin and
  because vLLM's OpenAI server is LiteLLM's best-trodden self-hosted path.
- **TGI / TensorRT-LLM**: TGI is in maintenance mode; TRT-LLM needs per-GPU engine builds, too heavy
  for a one-hour rental.
- **Ollama/llama.cpp**: no continuous batching at the level we want to measure.
- **Qwen3-8B**: good model, but EAGLE-3 heads for it are community-built and less tested.
- **vLLM's own `benchmark_serving`**: fine, but our script measures the same endpoint shape through
  LiteLLM too, and its JSON lands straight in RESULTS.

## Tradeoffs
- Prefix caching costs nothing on miss in vLLM V1, so the "off" arm exists only to quantify it.
- Speculative decoding trades compute for latency; expected to help at batch 1 and fade at 32.
- Our tenant-scoped response cache (ADR 011) sits in front; vLLM's prefix cache is cross-tenant at
  the KV level. It leaks nothing via outputs, but timing side channels on shared prefixes are a known
  risk; acceptable for a single-tenant GPU box, revisit before multi-tenant serving.
- Fallback to Haiku can surprise the bill during a GPU outage; LiteLLM logs the fallback.

## Verified here
`serving/bench.py` against the mock (tests/serving), LiteLLM config shape, `bash -n serve.sh`.
Compose smoke routes `local-vllm` through LiteLLM to the mock (CI only).
