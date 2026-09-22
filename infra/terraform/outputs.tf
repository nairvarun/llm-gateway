output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "region" {
  value = var.aws_region
}

output "vpc_id" {
  value = aws_vpc.staging.id
}

output "vpc_cidr" {
  value = aws_vpc.staging.cidr_block
}

output "ecr_repository_url" {
  value = aws_ecr_repository.gateway.repository_url
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "rds_endpoint" {
  value = aws_db_instance.staging.endpoint
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.staging.primary_endpoint_address
}

output "eks_cluster_name" {
  value = aws_eks_cluster.staging.name
}

output "eks_cluster_endpoint" {
  value = aws_eks_cluster.staging.endpoint
}

output "eks_cluster_version" {
  value = aws_eks_cluster.staging.version
}

output "eks_cluster_security_group_id" {
  value = aws_eks_cluster.staging.vpc_config[0].cluster_security_group_id
}

output "eks_admin_principal_arn" {
  value = aws_eks_access_entry.administrator.principal_arn
}

output "gateway_workload_role_arn" {
  value = aws_iam_role.gateway_workload.arn
}

output "gateway_smoke_role_arn" {
  value = aws_iam_role.gateway_smoke.arn
}

output "kubernetes_namespace" {
  value = var.kubernetes_namespace
}

output "runtime_image_repository" {
  value = aws_ecr_repository.gateway.repository_url
}

output "runtime_secret_names" {
  value = { for key, secret in aws_secretsmanager_secret.app : key => secret.name }
}

output "runtime_config" {
  value = {
    GATEWAY_PROVIDER                = "mock"
    GATEWAY_REDIS_URL               = "rediss://${aws_elasticache_replication_group.staging.primary_endpoint_address}:6379"
    GATEWAY_CACHE_REDIS_URL         = "rediss://${aws_elasticache_replication_group.staging.primary_endpoint_address}:6379"
    GATEWAY_REPLAY_RETENTION_HOURS  = "24"
    GATEWAY_CACHE_TTL_SECONDS       = "3600"
    GATEWAY_METADATA_RETENTION_DAYS = "30"
  }
}
