#!/usr/bin/env bash
# Phase 8 burst, teardown + bill check. Run it the same day, even if the drills failed.
# Order matters: the ALB and EBS volumes are created by controllers, not Terraform, and a
# leftover ALB/ENI blocks VPC deletion; so delete them through Kubernetes first.
set -uo pipefail
TF=deploy/terraform/eks
REGION=$(terraform -chdir=$TF output -raw region 2>/dev/null || echo "${AWS_REGION:-us-east-1}")

if kubectl version --request-timeout=10s >/dev/null 2>&1; then
  kubectl -n argocd delete application pap --wait=true --timeout=5m || true
  kubectl -n pap delete ingress --all --wait=true --timeout=5m || true          # -> ALB deleted
  kubectl -n pap delete postgrescluster --all --wait=true --timeout=10m || true # -> PVC/EBS deleted
  kubectl delete pvc --all -A --wait=true --timeout=5m || true
fi

terraform -chdir=$TF destroy -input=false -auto-approve
rc=$?

echo "== leftovers tagged project=pap (must be empty) =="
left=$(aws resourcegroupstaggingapi get-resources --region "$REGION" --tag-filters Key=project,Values=pap \
  --query 'ResourceTagMappingList[].ResourceARN' --output text | tr '\t' '\n' | grep -v ':kms:' || true)
# The KMS key sits in a 7-day pending-deletion window (not billed) and is excluded above.
echo "${left:-none}"
echo "== untagged controller leftovers (must be empty) =="
aws elbv2 describe-load-balancers --region "$REGION" --query 'LoadBalancers[?starts_with(LoadBalancerName, `k8s-pap`)].LoadBalancerArn' --output text
aws ec2 describe-volumes --region "$REGION" --filters Name=status,Values=available \
  --query 'Volumes[?Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]].VolumeId' --output text
echo "== cost so far this month (Cost Explorer lags ~24h; each call costs \$0.01) =="
aws ce get-cost-and-usage --time-period Start="$(date -u +%Y-%m-01)",End="$(date -u -d tomorrow +%F 2>/dev/null || date -u -v+1d +%F)" \
  --granularity MONTHLY --metrics UnblendedCost --query 'ResultsByTime[0].Total.UnblendedCost' --output text

[ $rc -eq 0 ] && [ -z "$left" ] || { echo "TEARDOWN INCOMPLETE: fix and re-run this script" >&2; exit 1; }
echo "Teardown complete. Check the Billing console again tomorrow."
