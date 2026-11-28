#!/usr/bin/env bash
# Phase 8 burst preflight (free: read-only AWS calls only). Run it in every new terminal after
# `source ~/pap-burst.env`; scripts/burst_up.sh runs it first. Exits non-zero on any FAIL.
set -uo pipefail
export AWS_PAGER=""
fail=0
ok() { echo "ok    $*"; }
bad() { echo "FAIL  $*"; fail=1; }
warn() { echo "warn  $*"; }

# Tool pins: ADR 014. terraform is pinned by required_version "~> 1.13.0": 1.14+ refuses to run.
need() { command -v "$1" >/dev/null 2>&1 || { bad "$1 not installed (runbook step 0)"; return 1; }; }
if need terraform; then
  v=$(terraform version -json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["terraform_version"])')
  case "$v" in 1.13.*) ok "terraform $v" ;; *) bad "terraform $v, need 1.13.x (modules pin ~> 1.13.0)" ;; esac
fi
if need helm; then
  v=$(helm version --short 2>/dev/null)
  case "$v" in v3.19.*) ok "helm $v" ;; v3.*) warn "helm $v, tested pin is 3.19" ;; *) bad "helm $v, need 3.x" ;; esac
fi
if need kubectl; then
  v=$(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["clientVersion"]["gitVersion"])')
  case "$v" in v1.33.*|v1.34.*|v1.35.*) ok "kubectl $v" ;; *) bad "kubectl $v, need within one minor of 1.34" ;; esac
fi
need aws && { case "$(aws --version 2>&1)" in aws-cli/2.*) ok "$(aws --version 2>&1 | cut -d' ' -f1)" ;; *) bad "aws CLI v2 needed" ;; esac; }
for t in docker envsubst python3 uvx openssl curl; do need "$t" && ok "$t"; done
command -v docker >/dev/null && { docker info >/dev/null 2>&1 && ok "docker daemon up" || bad "docker daemon not reachable (start Docker Desktop, WSL integration on)"; }

# Windows checkouts: CRLF in a .sh breaks bash with "$'\r': command not found".
if grep -lq $'\r' scripts/*.sh deploy/pgbackrest/*.sh 2>/dev/null; then
  bad "CRLF line endings in scripts: clone inside WSL (~/), not under /mnt/c"
else ok "scripts have LF endings"; fi

# Variables that later steps read. A new terminal without `source ~/pap-burst.env` loses them,
# and Locust then signs tokens with its fallback secret: every request 401s.
for v in AWS_REGION BURST_CONFIRM TF_VAR_allowed_cidr TF_VAR_alert_email TF_VAR_expires_on REPO_URL IMAGE_TAG LOAD_JWT_SECRET; do
  [ -n "${!v:-}" ] && ok "\$$v set" || bad "\$$v unset: source ~/pap-burst.env"
done
[ "${TF_VAR_expires_on:-}" = "$(date -u +%F)" ] || warn "TF_VAR_expires_on is not today (UTC)"
[ "${PAP_OIDC_ISSUER:-}" = "https://burst.invalid/realms/pap" ] || bad "PAP_OIDC_ISSUER must be https://burst.invalid/realms/pap (Locust signs with it)"

# AWS identity: an IAM user/role, never root.
arn=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null) || { bad "aws sts failed: run aws configure"; arn=""; }
case "$arn" in "") ;; *:root) bad "signed in as root: use the IAM user" ;; *) ok "identity ${arn##*/}" ;; esac

if [ -n "$arn" ]; then
  # Standard support only: extended support is $0.60/h instead of $0.10/h.
  k=${TF_VAR_kubernetes_version:-1.34}
  if aws eks describe-cluster-versions --region "$AWS_REGION" \
      --query 'clusterVersions[?status==`STANDARD_SUPPORT`].clusterVersion' --output text 2>/dev/null | tr '\t' '\n' | grep -qx "$k"; then
    ok "EKS $k in standard support"
  else bad "EKS $k not in standard support (or CLI too old): set TF_VAR_kubernetes_version"; fi
  # New accounts often have small vCPU quotas; 2x t3.large needs 4.
  cap=${TF_VAR_node_capacity_type:-SPOT}
  code=$([ "$cap" = SPOT ] && echo L-34B43A08 || echo L-1216C47A)
  q=$(aws service-quotas get-service-quota --region "$AWS_REGION" --service-code ec2 --quota-code "$code" \
      --query 'Quota.Value' --output text 2>/dev/null || echo 0)
  python3 -c "import sys;sys.exit(0 if float(sys.argv[1])>=4 else 1)" "$q" \
    && ok "$cap vCPU quota $q (need 4)" \
    || bad "$cap vCPU quota is $q, need 4: request an increase in Service Quotas or switch capacity type"
fi

[ $fail -eq 0 ] && echo "PREFLIGHT OK" || echo "PREFLIGHT FAILED: fix the FAIL lines first"
exit $fail
