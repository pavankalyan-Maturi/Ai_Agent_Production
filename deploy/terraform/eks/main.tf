locals {
  tags = {
    project    = "pap"
    burst      = "true"
    expires_on = var.expires_on
  }
  azs = slice(data.aws_availability_zones.this.names, 0, 2)
}

data "aws_availability_zones" "this" {
  state = "available"
}

# ---------------- network: public subnets only, no NAT gateway (cost guard) ----------------
# EKS needs control-plane subnets in two AZs; nodes run in the first one only (P8.1: 1 AZ).
resource "aws_vpc" "this" {
  cidr_block           = "10.80.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
}

# Public IPs on nodes are the no-NAT tradeoff (ADR 014); ingress is limited to allowed_cidr.
#trivy:ignore:AVD-AWS-0164
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 4, count.index)
  map_public_ip_on_launch = true
  tags = {
    Name                                = "${var.name}-public-${count.index}"
    "kubernetes.io/role/elb"            = "1"
    "kubernetes.io/cluster/${var.name}" = "shared"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------- EKS ----------------
data "aws_iam_policy_document" "eks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.name}-cluster"
  assume_role_policy = data.aws_iam_policy_document.eks_assume.json
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# Public endpoint restricted to allowed_cidr (public_access_cidrs); a bastion/VPN is out of scope.
#trivy:ignore:AVD-AWS-0040
resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.kubernetes_version
  role_arn = aws_iam_role.cluster.arn

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  vpc_config {
    subnet_ids              = aws_subnet.public[*].id
    endpoint_public_access  = true
    endpoint_private_access = true
    public_access_cidrs     = [var.allowed_cidr]
  }

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.eks.arn
    }
  }

  # Standard support only: extended support bills $0.60/h instead of $0.10/h.
  upgrade_policy {
    support_type = "STANDARD"
  }

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

# Envelope encryption for k8s Secrets. ~$1/month prorated; keys pending deletion are not billed.
resource "aws_kms_key" "eks" {
  description             = "${var.name} EKS secrets"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name}-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "default"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = [aws_subnet.public[0].id]
  capacity_type   = var.node_capacity_type
  instance_types  = [var.node_instance_type]
  disk_size       = 30

  scaling_config {
    desired_size = var.node_count
    min_size     = 1
    max_size     = var.node_count
  }

  depends_on = [aws_iam_role_policy_attachment.node]
}

resource "aws_eks_addon" "core" {
  for_each     = toset(["vpc-cni", "coredns", "kube-proxy"])
  cluster_name = aws_eks_cluster.this.name
  addon_name   = each.value
  depends_on   = [aws_eks_node_group.this]
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.irsa["ebs_csi"].arn
  depends_on               = [aws_eks_node_group.this]
}

# ---------------- IRSA ----------------
resource "aws_iam_openid_connect_provider" "eks" {
  url            = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  oidc_host = replace(aws_iam_openid_connect_provider.eks.url, "https://", "")
  irsa = {
    ebs_csi    = "system:serviceaccount:kube-system:ebs-csi-controller-sa"
    lbc        = "system:serviceaccount:kube-system:aws-load-balancer-controller"
    pgbackrest = "system:serviceaccount:pap:pap-db-*"
  }
}

data "aws_iam_policy_document" "irsa" {
  for_each = local.irsa
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
    }
    condition {
      test     = "StringLike"
      variable = "${local.oidc_host}:sub"
      values   = [each.value]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "irsa" {
  for_each           = local.irsa
  name               = "${var.name}-${replace(each.key, "_", "-")}"
  assume_role_policy = data.aws_iam_policy_document.irsa[each.key].json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.irsa["ebs_csi"].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# Policy file is upstream's, pinned to the controller version (ADR 014).
resource "aws_iam_policy" "lbc" {
  name   = "${var.name}-lbc"
  policy = file("${path.module}/lbc_iam_policy.json")
}

resource "aws_iam_role_policy_attachment" "lbc" {
  role       = aws_iam_role.irsa["lbc"].name
  policy_arn = aws_iam_policy.lbc.arn
}

# ---------------- pgBackRest repo (S3) ----------------
resource "aws_s3_bucket" "pgbackrest" {
  bucket_prefix = "${var.name}-pgbackrest-"
  force_destroy = true # cost guard: destroy must not stall on a non-empty bucket
}

resource "aws_s3_bucket_public_access_block" "pgbackrest" {
  bucket                  = aws_s3_bucket.pgbackrest.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 is enough for a one-day drill bucket; a CMK adds a key to manage and destroy.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "pgbackrest" {
  bucket = aws_s3_bucket.pgbackrest.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "pgbackrest" {
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.pgbackrest.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.pgbackrest.arn}/*"]
  }
}

resource "aws_iam_role_policy" "pgbackrest" {
  role   = aws_iam_role.irsa["pgbackrest"].name
  policy = data.aws_iam_policy_document.pgbackrest.json
}

# ---------------- Secrets Manager + ECR ----------------
# Container only: the value is put by the human with the AWS CLI (runbook step 3), never by
# Terraform, so it never lands in state. burst_up.sh copies it into a k8s Secret with the
# operator's credentials, so no pod needs Secrets Manager access. Zero recovery window so
# destroy really deletes it.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app"
  recovery_window_in_days = 0
}

resource "aws_ecr_repository" "api" {
  name                 = "${var.name}/pap-api"
  force_delete         = true
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

# ---------------- cost guard ----------------
resource "aws_budgets_budget" "burst" {
  name         = "${var.name}-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
