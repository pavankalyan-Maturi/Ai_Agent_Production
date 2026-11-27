# Runbook: Phase 8 one-day AWS burst (human-run)

Status: written and statically checked only. Nothing here has run against AWS. Every price is a
list-price ESTIMATE (us-east-1, Nov 2026, UNVERIFIED); check the AWS Pricing Calculator on the day.
Design and pins: ADR 014. CHECKPOINT: do not start before you have a free day and a budget.

## Cost estimate (ESTIMATE)

| Item | Spot nodes | On-demand nodes |
|---|---|---|
| EKS control plane (standard support) | $0.10/h | $0.10/h |
| 2x t3.large nodes | ~$0.06/h | $0.17/h |
| ALB (hourly + light LCU) | ~$0.03/h | ~$0.03/h |
| Public IPv4 (~5 addresses x $0.005) | ~$0.025/h | ~$0.025/h |
| EBS gp3 (~80 GB incl. db + restore) | ~$0.01/h | ~$0.01/h |
| S3, ECR, Secrets Manager, Cost Explorer calls | < $0.10 total | < $0.10 total |
| **Total** | **~$0.23/h, ~$1.50 for 6 h** | **~$0.34/h, ~$2.10 for 6 h** |

k3s-on-EC2 alternative (`deploy/terraform/k3s`): one t3.xlarge spot ~$0.08/h all-in, ~$0.50 for
6 h; no EKS, no ALB, no IRSA, so it proves less. Forgetting teardown is the real risk: the EKS
stack costs ~$5.50-8/day idle. NAT gateways are not used (would add ~$1.10/day + data).

## Cost-guard checklist (all must be ticked before step 2)

- [ ] Fresh AWS account or one with nothing tagged `project=pap`; root has MFA; you work as an
      IAM user/role, never root.
- [ ] Billing alerts on (Billing > Preferences). The Terraform budget (`budget_usd`, default $10)
      emails at 50% and 100%; it only exists while the stack does.
- [ ] Phone alarm set for teardown time (start + 6 h). EKS has no auto-off; k3s powers off after 10 h.
- [ ] `aws eks describe-cluster-versions --query 'clusterVersions[?status==`STANDARD_SUPPORT`].clusterVersion'`
      includes `kubernetes_version` (default 1.34). Extended support is $0.60/h.
- [ ] Chart versions in `scripts/burst_up.sh` still resolve (`helm search repo ... --versions`).
- [ ] `TF_VAR_allowed_cidr` is your IP/32 (`curl -s https://checkip.amazonaws.com`).
- [ ] You will run `scripts/burst_destroy.sh` the same day even if a drill fails.

## 0. Tools (laptop)
aws CLI v2, terraform 1.13.4, helm 3.19, kubectl 1.34, envsubst (gettext), docker, uv.

## 1. Variables
```bash
export AWS_REGION=us-east-1 BURST_CONFIRM=yes
export TF_VAR_allowed_cidr="$(curl -s https://checkip.amazonaws.com)/32"
export TF_VAR_alert_email=you@example.com TF_VAR_expires_on=$(date -u +%F)
export REPO_URL=https://github.com/pavankalyan-Maturi/Ai_Agent_Production GIT_REVISION=main
export IMAGE_TAG=p8-$(git rev-parse --short HEAD)
```
If the repo is private, add it to Argo CD after step 4 (`argocd repo add` with a read-only token).

## 2. Registry + secret first (billing starts; pennies)
```bash
terraform -chdir=deploy/terraform/eks init
terraform -chdir=deploy/terraform/eks apply -target=aws_ecr_repository.api -target=aws_secretsmanager_secret.app
ECR=$(terraform -chdir=deploy/terraform/eks output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${ECR%%/*}"
docker build -t "$ECR:$IMAGE_TAG" . && docker push "$ECR:$IMAGE_TAG"
export LOAD_JWT_SECRET=$(openssl rand -hex 32)
aws secretsmanager put-secret-value --secret-id pap-burst/app --secret-string "$(python3 -c '
import json,os; print(json.dumps({"PAP_LLM_API_KEY":"burst-mock","PAP_OIDC_KEY":os.environ["LOAD_JWT_SECRET"],
"PAP_OIDC_ALGORITHMS":"[\"HS256\"]","PAP_OIDC_ISSUER":"https://burst.invalid/realms/pap"}))')"
```
HS256 load-test tokens are acceptable only because the ALB admits your IP alone and the stack
lives for hours. The LLM is the mock: no paid model calls.

## 3. Bring up (~20-25 min: EKS ~12, node group ~4, charts ~5)
```bash
scripts/burst_up.sh 2>&1 | tee burst_up.log
kubectl -n argocd get application pap -w        # Synced / Healthy
HOST=$(kubectl -n pap get ingress pap-api -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
curl -sf "http://$HOST/readyz"
```
Evidence: `burst_up.log` tail, `kubectl get nodes,pods -A -o wide`.

## 4. KEDA scale-out under Locust
```bash
kubectl -n pap get hpa,scaledobject -w &        # KEDA creates keda-hpa-pap-api
cd loadtest && PAP_OIDC_ISSUER=https://burst.invalid/realms/pap uvx --from locust==2.47.0 --with pyjwt \
  locust -f locustfile.py --headless -u 100 -r 10 -t 600s -H "http://$HOST" --only-summary --csv ../deploy/results/scale
```
Pass: replicas go 2 -> >2 within ~2 min of CPU > 60%, and back to 2 after load (5 min cooldown).
Record time-to-scale and p95 from `scale_stats.csv`.

## 5. Chaos: one pod-kill experiment under load
Start the same Locust command (`--csv ../deploy/results/chaos`), then:
```bash
kubectl apply -f deploy/chaos/pod-kill.yaml && sleep 600 && kubectl delete -f deploy/chaos/pod-kill.yaml
```
Pass: failure ratio < 1% and p95 within 2x the step-4 baseline. PDB keeps one pod serving.

## 6. pgBackRest restore drill (RTO)
```bash
export PGBACKREST_BUCKET=$(terraform -chdir=deploy/terraform/eks output -raw pgbackrest_bucket)
export PGBACKREST_ROLE_ARN=$(terraform -chdir=deploy/terraform/eks output -json irsa_role_arns | python3 -c 'import json,sys;print(json.load(sys.stdin)["pgbackrest"])')
deploy/pgbackrest/restore_drill.sh               # writes deploy/pgbackrest/results/restore_drill.json
```
Pass: `marker_found: 1`. RTO = `rto_seconds` (10 GiB volume, near-empty DB: a floor, not a
production number).

## 7. Teardown + bill check (mandatory, same day)
```bash
scripts/burst_destroy.sh 2>&1 | tee burst_destroy.log
```
It deletes the Argo app, Ingress (ALB) and PostgresClusters (EBS) through Kubernetes first,
then `terraform destroy`, then lists anything still tagged `project=pap`, stray `k8s-pap*` ALBs
and orphaned PVC volumes. Anything printed there: delete it in the console and re-run.
Check Billing > Bills the next day (Cost Explorer lags ~24 h). For k3s:
`terraform -chdir=deploy/terraform/k3s destroy`.

## 8. Evidence to commit (P8.3)
`deploy/results/*` (Locust CSVs), `deploy/pgbackrest/results/restore_drill.json`, the tails of
`burst_up.log` / `burst_destroy.log` (no account ids), and the next-day bill. Paste them in the
thread; Claude fills docs/RESULTS.md.

## Known unverified points
- PGO `backups.pgbackrest.metadata.annotations` reaching the pgBackRest service accounts for IRSA,
  and PGO chart 5.8.2 existing: not checked against a live operator.
- Mock LLM reachable at `PAP_LLM_GATEWAY_URL=http://pap-mock-llm:8010` (same `/v1` paths as
  LiteLLM) and the HS256 issuer override: as in compose, not run on k8s.
- Spot capacity for t3.large on the day; switch `TF_VAR_node_capacity_type=ON_DEMAND` if short.
