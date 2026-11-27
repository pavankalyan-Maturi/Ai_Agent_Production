#!/usr/bin/env bash
# Lint the chart and schema-check every Phase 8 manifest offline-ish (schemas from GitHub raw).
# Usage: scripts/check_manifests.sh [path/to/kubeconform]. Needs helm and python3.
set -euo pipefail
KC=${1:-kubeconform}
S=$(mktemp -d)
export ECR_REPOSITORY_URL=111111111111.dkr.ecr.us-east-1.amazonaws.com/pap IMAGE_TAG=ci PGBACKREST_BUCKET=ci \
  AWS_REGION=us-east-1 PGBACKREST_ROLE_ARN=arn:aws:iam::111111111111:role/ci ALLOWED_CIDR=192.0.2.1/32 \
  REPO_URL=https://github.com/example/repo GIT_REVISION=main
es() { python3 -c "import os,sys;print(os.path.expandvars(open(sys.argv[1]).read()),end='')" "$1"; }

es deploy/helm/pap/values-eks.yaml > "$S/eks.yaml"
helm lint deploy/helm/pap
helm lint deploy/helm/pap -f "$S/eks.yaml"
helm template pap deploy/helm/pap -n pap > "$S/default.yaml"
helm template pap deploy/helm/pap -n pap -f "$S/eks.yaml" > "$S/eks-rendered.yaml"
{ es deploy/argocd/application.yaml; es deploy/helm/pap/values-eks.yaml | sed 's/^/        /'; } > "$S/app.yaml"
es deploy/pgbackrest/restore-cluster.yaml > "$S/restore.yaml"

"$KC" -strict -summary -kubernetes-version 1.34.0 -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  "$S/default.yaml" "$S/eks-rendered.yaml" "$S/app.yaml" "$S/restore.yaml"

# Chaos Mesh CRDs are not in the catalog: build the Schedule schema from the pinned CRD.
curl -sSfL https://raw.githubusercontent.com/chaos-mesh/chaos-mesh/v2.7.3/manifests/crd.yaml -o "$S/crd.yaml"
python3 - "$S" <<'PY'
import json, sys, yaml
s = sys.argv[1]
for d in yaml.safe_load_all(open(f"{s}/crd.yaml")):
    if d and d.get("kind") == "CustomResourceDefinition" and d["spec"]["names"]["kind"] == "Schedule":
        for v in d["spec"]["versions"]:
            json.dump(v["schema"]["openAPIV3Schema"], open(f"{s}/schedule_{v['name']}.json", "w"))
PY
"$KC" -strict -summary -schema-location "$S/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" deploy/chaos/pod-kill.yaml
