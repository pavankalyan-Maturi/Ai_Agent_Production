#!/usr/bin/env bash
# Phase 8 burst, bring-up (human-run; docs/runbooks/aws-burst.md). Spends real money from the
# moment `terraform apply` starts. Teardown: scripts/burst_destroy.sh, the same day.
set -euo pipefail
[ "${BURST_CONFIRM:-}" = "yes" ] || { echo "Set BURST_CONFIRM=yes after reading the cost-guard checklist." >&2; exit 1; }
: "${TF_VAR_allowed_cidr:?your IP/32}" "${TF_VAR_alert_email:?}" "${TF_VAR_expires_on:?YYYY-MM-DD}"
: "${IMAGE_TAG:?pushed image tag}" "${REPO_URL:?git URL Argo CD can read}"
GIT_REVISION=${GIT_REVISION:-main}
export AWS_PAGER=""  # aws CLI v2 otherwise opens a pager and the script hangs
scripts/burst_preflight.sh || exit 1
TF=deploy/terraform/eks
aws sts get-caller-identity --query Account --output text

# Versions: ADR 014. Verify each still exists with `helm search repo <chart> --versions` first.
LBC_CHART=3.2.0 METRICS_CHART=3.13.0 KEDA_CHART=2.18.1 ARGO_CHART=9.1.0 CHAOS_CHART=2.7.3 PGO_CHART=5.8.2

terraform -chdir=$TF init -input=false
terraform -chdir=$TF apply -input=false -auto-approve
out() { terraform -chdir=$TF output -raw "$1"; }
CLUSTER=$(out cluster_name) AWS_REGION=$(out region)
export AWS_REGION ECR_REPOSITORY_URL=$(out ecr_repository_url) PGBACKREST_BUCKET=$(out pgbackrest_bucket)
roles=$(terraform -chdir=$TF output -json irsa_role_arns)
role() { python3 -c "import json,sys;print(json.loads(sys.argv[1])[sys.argv[2]])" "$roles" "$1"; }
export PGBACKREST_ROLE_ARN=$(role pgbackrest) LBC_ROLE_ARN=$(role lbc)
export ALLOWED_CIDR=$TF_VAR_allowed_cidr IMAGE_TAG REPO_URL GIT_REVISION
aws eks update-kubeconfig --name "$CLUSTER" --region "$AWS_REGION"

# Default gp3 StorageClass (EKS no longer marks gp2 default).
kubectl apply -f - <<'YAML'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
  annotations: {storageclass.kubernetes.io/is-default-class: "true"}
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Delete
parameters: {type: gp3}
YAML

helm repo add eks https://aws.github.io/eks-charts
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo add kedacore https://kedacore.github.io/charts
helm repo add argo https://argoproj.github.io/argo-helm
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update
VPC_ID=$(out vpc_id)
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller -n kube-system \
  --version $LBC_CHART --set clusterName="$CLUSTER" --set region="$AWS_REGION" --set vpcId="$VPC_ID" \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="$LBC_ROLE_ARN" --wait
helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system --version $METRICS_CHART --wait
helm upgrade --install keda kedacore/keda -n keda --create-namespace --version $KEDA_CHART --wait
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace --version $CHAOS_CHART \
  --set chaosDaemon.runtime=containerd --set chaosDaemon.socketPath=/run/containerd/containerd.sock --wait
helm upgrade --install pgo oci://registry.developers.crunchydata.com/crunchydata/pgo -n pgo --create-namespace \
  --version $PGO_CHART --wait
helm upgrade --install argocd argo/argo-cd -n argocd --create-namespace --version $ARGO_CHART --wait

# App secret: Secrets Manager -> k8s Secret (the value never touches git or Terraform state).
kubectl create namespace pap --dry-run=client -o yaml | kubectl apply -f -
aws secretsmanager get-secret-value --secret-id "$(out secret_arn)" --query SecretString --output text \
  | python3 -c "import json,sys;[print(f'{k}={v}') for k,v in json.load(sys.stdin).items()]" \
  | kubectl -n pap create secret generic pap-app --from-env-file=/dev/stdin --dry-run=client -o yaml \
  | kubectl apply -f -

{ envsubst < deploy/argocd/application.yaml; envsubst < deploy/helm/pap/values-eks.yaml | sed 's/^/        /'; } \
  | kubectl apply -f -
echo "Argo CD syncing; watch: kubectl -n argocd get application pap -w"
echo "ALB: kubectl -n pap get ingress pap-api -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'"
