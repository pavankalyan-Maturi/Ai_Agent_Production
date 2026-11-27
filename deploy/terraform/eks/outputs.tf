output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "region" {
  value = var.region
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "pgbackrest_bucket" {
  value = aws_s3_bucket.pgbackrest.bucket
}

output "secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "irsa_role_arns" {
  value = { for k, r in aws_iam_role.irsa : k => r.arn }
}
