# ADR 014: Phase 8 AWS burst as code, human-run

Status: accepted (code + runbook). Nothing applied: no AWS account or budget in this thread.
All numbers NOT MEASURED; prices are list-price ESTIMATES.

## Decision
- **Terraform 1.13.4, hashicorp/aws 6.20.0 (exact), raw resources, no registry modules.**
  `terraform-aws-modules/eks` would save ~150 lines but pulls ~10 sub-modules and providers to
  audit; for one cluster, explicit resources are easier to defend and to read in review.
- **Local state.** The stack lives hours; an S3+DynamoDB backend is a second stack to bootstrap and
  destroy. Tradeoff: state is on one laptop; losing it mid-burst means manual cleanup by tag
  (`project=pap`, which `burst_destroy.sh` lists).
- **Network: public subnets, no NAT gateway.** Saves ~$1.10/day + data. Nodes get public IPs;
  the EKS endpoint and the ALB only admit `allowed_cidr`. Production would use private node
  subnets + NAT or VPC endpoints. Control plane spans 2 AZs (EKS requirement); nodes 1 AZ (P8.1).
- **EKS 1.34, standard support pinned** (`upgrade_policy`), API auth mode, IRSA for the EBS CSI
  driver, AWS Load Balancer Controller and pgBackRest. Spot t3.large x2 by default.
- **Add-ons by Helm CLI, the app by Argo CD.** Pins: LBC chart 3.2.0 (app v3.2.0, IAM policy file
  from the same tag), metrics-server 3.13.0, KEDA 2.18.1, Argo CD chart 9.1.0 (v3.2.0), Chaos Mesh
  2.7.3, Crunchy PGO 5.8.2 (UNVERIFIED: OCI registry not reachable here). Bootstrapping add-ons
  through Argo CD (app-of-apps) is the production pattern; for one day it adds ordering problems
  (CRDs before CRs) without teaching more.
- **Postgres: Crunchy PGO**, because pgBackRest is its native backup engine (CloudNativePG uses
  Barman) and it bundles pgvector and PgBouncer. RDS would make the restore drill a console click.
- **Secrets:** Terraform creates the Secrets Manager container only; the value is put by the
  operator and copied into a k8s Secret by `burst_up.sh`. External Secrets Operator is the
  production answer; for one day it is another controller and CRD set to run.
- **KEDA with a CPU trigger only.** Honest note: for CPU alone KEDA is an HPA wrapper and does not
  earn its place; it is here because the spec asks for it and the next trigger (RabbitMQ queue
  depth for ingest workers, scale-to-zero) is where it does.
- **One Chaos Mesh experiment** (pod-kill every 60 s for 10 min under Locust), per SPEC non-goals.
- **Mock LLM in the burst.** No GPU and no paid model calls; the burst tests the platform, not
  the model.
- **k3s-on-EC2 alternative** (`deploy/terraform/k3s`, k3s v1.34.1+k3s1): ~1/3 of the cost,
  self-terminating after 10 h, but no EKS/ALB/IRSA to show.

## Cost guards (enforced in code, tested in tests/infra)
No NAT; `allowed_cidr` cannot be 0.0.0.0/0; 1-4 nodes; standard support only; AWS Budget with
email at 50/100%; `force_destroy` on S3/ECR and 0-day secret recovery so destroy completes;
`burst_up.sh` needs `BURST_CONFIRM=yes`; `burst_destroy.sh` removes ALB/EBS via Kubernetes before
`terraform destroy`, then lists leftovers and fails loudly.

## Verified here
`terraform fmt/validate` + `tflint` (both modules, provider from releases.hashicorp.com mirror),
`helm lint` + `helm template` (default and EKS values), `kubeconform -strict` on every rendered
manifest (k8s 1.34 + CRD catalog; Chaos Mesh schema from its v2.7.3 CRD), `bash -n` on scripts,
8 static tests. CI job `infra.yml` repeats these.

## Not verified
Any apply, sync, scale-out, chaos or restore; PGO IRSA wiring; chart versions beyond the four
whose Chart.yaml was fetched at the tag (Argo CD, KEDA, LBC, metrics-server); all prices.
