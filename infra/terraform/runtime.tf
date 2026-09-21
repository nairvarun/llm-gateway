resource "aws_lb" "staging" {
  count                      = var.acm_certificate_arn == null ? 0 : 1
  name                       = "${var.name}-alb"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = [for subnet in aws_subnet.public : subnet.id]
  enable_deletion_protection = true
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "gateway" {
  count       = var.acm_certificate_arn == null ? 0 : 1
  name        = "${var.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.staging.id

  health_check {
    path                = "/health/ready"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 30
    timeout             = 5
  }
}

resource "aws_lb_listener" "https" {
  count             = var.acm_certificate_arn == null ? 0 : 1
  load_balancer_arn = aws_lb.staging[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway[0].arn
  }
}

resource "aws_ecs_cluster" "staging" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid       = "OnlyGatewayRuntimeSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for secret in aws_secretsmanager_secret.app : secret.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "artifact_access" {
  statement {
    sid       = "EvaluationArtifactObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/evaluations/*"]
  }
  statement {
    sid       = "ListEvaluationPrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["evaluations/*"]
    }
  }
}

resource "aws_iam_role_policy" "artifact_access" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.artifact_access.json
}

locals {
  runtime_environment = [
    { name = "GATEWAY_PROVIDER", value = "mock" },
    { name = "GATEWAY_RUN_MIGRATIONS", value = "false" },
    { name = "GATEWAY_REDIS_URL", value = "rediss://${aws_elasticache_replication_group.staging.primary_endpoint_address}:6379" },
    { name = "GATEWAY_REPLAY_RETENTION_HOURS", value = "24" },
    { name = "GATEWAY_CACHE_TTL_SECONDS", value = "3600" },
    { name = "GATEWAY_METADATA_RETENTION_DAYS", value = "30" }
  ]
  runtime_secrets = [
    { name = "GATEWAY_DATABASE_URL", valueFrom = aws_secretsmanager_secret.app["database-url"].arn },
    { name = "GATEWAY_REPLAY_ENCRYPTION_KEY", valueFrom = aws_secretsmanager_secret.app["replay-key"].arn },
    { name = "GATEWAY_CACHE_ENCRYPTION_KEY", valueFrom = aws_secretsmanager_secret.app["cache-key"].arn },
    { name = "GATEWAY_INPUT_HASH_KEY", valueFrom = aws_secretsmanager_secret.app["input-hash-key"].arn }
  ]
  common_container = {
    name         = "gateway"
    image        = var.runtime_image_digest == null ? "" : "${aws_ecr_repository.gateway.repository_url}@${var.runtime_image_digest}"
    essential    = true
    environment  = local.runtime_environment
    secrets      = local.runtime_secrets
    portMappings = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.gateway.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "gateway"
      }
    }
  }
}

resource "aws_ecs_task_definition" "api" {
  count                    = var.runtime_image_digest == null ? 0 : 1
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions    = jsonencode([local.common_container])
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
}

resource "aws_ecs_task_definition" "worker" {
  count                    = var.runtime_image_digest == null ? 0 : 1
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([merge(local.common_container, {
    portMappings = []
    command      = ["gateway", "evaluate-worker", "--max-cases", "100"]
  })])
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
}

resource "aws_ecs_task_definition" "migration" {
  count                    = var.runtime_image_digest == null ? 0 : 1
  family                   = "${var.name}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([merge(local.common_container, {
    portMappings = []
    command      = ["sh", "-c", "gateway wait-database --timeout 30 && alembic upgrade head"]
  })])
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
}

resource "aws_ecs_service" "api" {
  count           = var.enable_runtime ? 1 : 0
  name            = "${var.name}-api"
  cluster         = aws_ecs_cluster.staging.id
  task_definition = aws_ecs_task_definition.api[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [for subnet in aws_subnet.app : subnet.id]
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway[0].arn
    container_name   = "gateway"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https]
}
