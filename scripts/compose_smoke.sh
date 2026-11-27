#!/usr/bin/env bash
# Boots compose profiles and asserts the Phase 0 acceptance criteria end to end.
# Usage: scripts/compose_smoke.sh [--llmobs]   (expects pap-api:dev image to exist)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
PROFILES=(--profile core --profile obs)
LLMOBS=0
if [ "${1:-}" = "--llmobs" ]; then PROFILES+=(--profile llmobs); LLMOBS=1; fi
C=(docker compose -f deploy/compose/docker-compose.yml --env-file .env "${PROFILES[@]}")

start=$(date +%s)
"${C[@]}" up -d --no-build --wait --wait-timeout 300
echo "BOOT_SECONDS=$(( $(date +%s) - start ))"

fail() { echo "SMOKE FAIL: $*"; "${C[@]}" logs --tail 50; exit 1; }

curl -fsS localhost:8000/healthz | grep -q '"ok"' || fail "/healthz"
curl -fsS localhost:8000/readyz | grep -q '"ready":true' || fail "/readyz"
for _ in $(seq 10); do curl -fsS localhost:8000/v1/ping >/dev/null; done

spans=0
for _ in $(seq 20); do
  spans=$(curl -fsS localhost:8888/metrics | awk '/^otelcol_receiver_accepted_spans_total/ {s+=$NF} END {print s+0}')
  [ "${spans%.*}" -gt 0 ] && break; sleep 2
done
[ "${spans%.*}" -gt 0 ] || fail "collector accepted no spans"
echo "COLLECTOR_ACCEPTED_SPANS=$spans"

curl -fsS localhost:9090/api/v1/targets | grep -q '"job":"pap-api"' || fail "prometheus target"
curl -fsS -u "admin:${GRAFANA_ADMIN_PASSWORD:-pap-dev-only}" localhost:3001/api/dashboards/uid/pap-api >/dev/null \
  || fail "grafana dashboard not provisioned"

# Phase 4: provider fallback (mock-flaky 503 -> mock) and streaming, through LiteLLM from the api.
"${C[@]}" exec -T api python - <<'PY' || fail "litellm fallback/stream"
import asyncio, os, httpx
from gateway.client import GatewayModel, StreamClient
url, key = os.environ["PAP_LLM_GATEWAY_URL"], os.environ["PAP_LLM_API_KEY"]
d = GatewayModel(url, key, name="mock-flaky").decide([{"role": "user", "content": "smoke"}], [])
assert d.text.startswith("mock answer"), d
async def s():
    async with httpx.AsyncClient(base_url=url) as h:
        return [x async for x in StreamClient(h, key).stream("mock", [{"role": "user", "content": "a b"}])]
assert len(asyncio.run(s())) >= 2
r = httpx.post(f"{url}/key/generate", headers={"Authorization": f"Bearer {key}"}, json={})
assert r.status_code == 403, r.status_code
print("LITELLM_FALLBACK_OK=1 ADMIN_ROUTE_BLOCKED=1")
PY

if [ "$LLMOBS" = 1 ]; then
  for _ in $(seq 60); do curl -fsS localhost:3000/api/public/health >/dev/null 2>&1 && break; sleep 5; done
  curl -fsS localhost:3000/api/public/health | grep -q '"OK"' || fail "langfuse health"
  # Langfuse may still be migrating when the first spans arrive, so send fresh ones and poll.
  n=0
  for _ in $(seq 30); do
    curl -fsS localhost:8000/v1/ping >/dev/null
    n=$(curl -fsS -u pk-lf-pap-dev:sk-lf-pap-dev 'localhost:3000/api/public/v2/observations?limit=1' \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("data", [])))' || echo 0)
    [ "$n" -gt 0 ] && break; sleep 5
  done
  [ "$n" -gt 0 ] || fail "no observations reached Langfuse"
  echo "LANGFUSE_OBSERVATIONS_OK=1"
fi
echo "SMOKE OK"
