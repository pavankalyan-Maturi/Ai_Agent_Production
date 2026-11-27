#!/usr/bin/env bash
# pgBackRest restore drill (P8.2). Writes a marker row, takes a full backup to S3, restores it
# into a new cluster and measures RTO = time from "restore requested" to "marker readable".
# Needs: kubectl context on the burst cluster, envsubst, and the env from scripts/burst_up.sh.
set -euo pipefail
: "${PGBACKREST_BUCKET:?}" "${AWS_REGION:?}" "${PGBACKREST_ROLE_ARN:?}"
NS=pap
OUT=${OUT:-deploy/pgbackrest/results/restore_drill.json}
here=$(cd "$(dirname "$0")" && pwd)

primary() {  # $1 = cluster name
  kubectl -n "$NS" get pod -l "postgres-operator.crunchydata.com/cluster=$1,postgres-operator.crunchydata.com/role=master" \
    -o jsonpath='{.items[0].metadata.name}'
}
psql_on() {  # $1 = cluster, $2 = SQL
  kubectl -n "$NS" exec "$(primary "$1")" -c database -- psql -d pap -Atc "$2"
}

marker="drill-$(date -u +%Y%m%dT%H%M%SZ)"
psql_on pap-db "CREATE TABLE IF NOT EXISTS drill_marker(id text PRIMARY KEY, at timestamptz DEFAULT now()); INSERT INTO drill_marker(id) VALUES ('$marker');"
rows_before=$(psql_on pap-db "SELECT count(*) FROM drill_marker;")

echo "full backup..."
kubectl -n "$NS" patch postgrescluster pap-db --type merge \
  -p '{"spec":{"backups":{"pgbackrest":{"manual":{"repoName":"repo1","options":["--type=full"]}}}}}'
kubectl -n "$NS" annotate postgrescluster pap-db --overwrite \
  postgres-operator.crunchydata.com/pgbackrest-backup="$(date +%s)"
kubectl -n "$NS" wait --for=condition=complete --timeout=20m \
  job -l postgres-operator.crunchydata.com/pgbackrest-backup=manual

echo "restore..."
t0=$(date +%s)
envsubst < "$here/restore-cluster.yaml" | kubectl apply -f -
until [ -n "$(primary pap-db-restore 2>/dev/null)" ] && \
      kubectl -n "$NS" wait --for=condition=Ready --timeout=10s pod "$(primary pap-db-restore)" >/dev/null 2>&1; do
  [ $(( $(date +%s) - t0 )) -gt 1800 ] && { echo "restore did not become ready in 30 min" >&2; exit 1; }
  sleep 5
done
found=$(psql_on pap-db-restore "SELECT count(*) FROM drill_marker WHERE id='$marker';")
t1=$(date +%s)

mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<JSON
{"marker": "$marker", "rows_before": $rows_before, "marker_found": $found, "rto_seconds": $((t1 - t0)),
 "measured_at": "$(date -u +%FT%TZ)", "source": "pap-db", "target": "pap-db-restore"}
JSON
cat "$OUT"
[ "$found" = "1" ] || { echo "FAIL: marker missing after restore" >&2; exit 1; }
kubectl -n "$NS" delete postgrescluster pap-db-restore --wait=false
