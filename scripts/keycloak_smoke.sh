#!/usr/bin/env bash
# Auth-profile smoke: Keycloak realm import -> client_credentials token -> api accepts it.
# Run after scripts/compose_smoke.sh (api already up). Dev-only credentials from the realm file.
set -euo pipefail
C=(docker compose -f deploy/compose/docker-compose.yml --env-file .env --profile core --profile auth)
"${C[@]}" up -d keycloak
KC=http://localhost:8080/realms/pap/protocol/openid-connect/token
tok=""
for _ in $(seq 1 90); do
  tok=$(curl -sf -d grant_type=client_credentials -d client_id=pap-demo \
    -d client_secret=pap-dev-only-secret "$KC" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' 2>/dev/null) && break
  sleep 2
done
[ -n "$tok" ] || { echo "FAIL: no token from keycloak"; exit 1; }
python3 - "$tok" <<'PY'
import base64, json, sys
p = sys.argv[1].split(".")[1]
c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
aud = c["aud"] if isinstance(c["aud"], list) else [c["aud"]]
assert c["iss"] == "http://localhost:8080/realms/pap", c["iss"]
assert "pap-api" in aud and c["tenant_id"] == "demo", c
assert "pap:chat" in c["scope"].split(), c["scope"]
print("token claims ok:", c["iss"], aud, c["tenant_id"], c["scope"])
PY
body='{"messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
api=http://localhost:8000/v1/chat/stream
anon=$(curl -s -o /dev/null -w '%{http_code}' -H 'content-type: application/json' -d "$body" "$api")
[ "$anon" = 401 ] || { echo "FAIL: anonymous call got $anon, want 401"; exit 1; }
code=$(curl -s -o /tmp/kc_stream.txt -w '%{http_code}' -H "authorization: Bearer $tok" \
  -H 'content-type: application/json' -d "$body" "$api")
[ "$code" = 200 ] || { echo "FAIL: authed call got $code"; head -c 400 /tmp/kc_stream.txt; exit 1; }
echo "PASS keycloak auth smoke: anon 401, token 200"
