output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "region" {
  value = var.aws_region
}

output "vpc_id" {
  value = aws_vpc.staging.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.gateway.repository_url
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "alb_dns_name" {
  value = var.acm_certificate_arn == null ? null : aws_lb.staging[0].dns_name
}

output "rds_endpoint" {
  value = aws_db_instance.staging.endpoint
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.staging.primary_endpoint_address
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.staging.name
}

output "runtime_enabled" {
  value = var.enable_runtime
}
