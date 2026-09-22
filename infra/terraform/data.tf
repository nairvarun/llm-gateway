resource "aws_db_subnet_group" "staging" {
  name       = "${var.name}-db"
  subnet_ids = [for subnet in aws_subnet.data : subnet.id]
}

resource "aws_db_parameter_group" "staging" {
  name   = "${var.name}-postgres16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "staging" {
  identifier                   = "${var.name}-postgres"
  engine                       = "postgres"
  engine_version               = "16.15"
  instance_class               = "db.t4g.micro"
  allocated_storage            = 20
  max_allocated_storage        = 40
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = "gateway"
  username                     = "gateway_admin"
  manage_master_user_password  = true
  db_subnet_group_name         = aws_db_subnet_group.staging.name
  parameter_group_name         = aws_db_parameter_group.staging.name
  vpc_security_group_ids       = [aws_security_group.database.id]
  publicly_accessible          = false
  multi_az                     = false
  backup_retention_period      = var.db_backup_retention_days
  copy_tags_to_snapshot        = true
  deletion_protection          = true
  skip_final_snapshot          = false
  final_snapshot_identifier    = "${var.name}-final"
  auto_minor_version_upgrade   = false
  performance_insights_enabled = false
  monitoring_interval          = 0
  apply_immediately            = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_elasticache_subnet_group" "staging" {
  name       = "${var.name}-redis"
  subnet_ids = [for subnet in aws_subnet.data : subnet.id]
}

resource "aws_elasticache_replication_group" "staging" {
  replication_group_id       = "${var.name}-redis"
  description                = "Private staging correctness controls and optional cache"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  subnet_group_name          = aws_elasticache_subnet_group.staging.name
  security_group_ids         = [aws_security_group.redis.id]
  snapshot_retention_limit   = 1
  apply_immediately          = false
  auto_minor_version_upgrade = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.name}-${var.expected_account_id}-${var.aws_region}-artifacts"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "bounded-evaluation-artifacts"
    status = "Enabled"
    filter {}
    expiration {
      days = var.artifact_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.artifacts]
}

resource "aws_s3_bucket_policy" "tls_only" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

resource "aws_ecr_repository" "gateway" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "gateway" {
  repository = aws_ecr_repository.gateway.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the newest 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_secretsmanager_secret" "app" {
  for_each = toset([
    "database-url",
    "replay-key",
    "cache-key",
    "input-hash-key",
    "smoke-client-key",
    "tls-cert",
    "tls-key",
  ])
  name                    = "${var.name}/${each.key}"
  description             = "Operator-supplied staging secret; Terraform never stores its value"
  recovery_window_in_days = 30
}

resource "aws_budgets_budget" "staging" {
  name         = "${var.name}-monthly-alert"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.budget_alert_email == null ? [] : [var.budget_alert_email]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = 80
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [notification.value]
    }
  }
}
