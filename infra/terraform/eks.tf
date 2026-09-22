data "aws_iam_policy_document" "eks_cluster_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks_cluster" {
  name               = "${var.name}-eks-cluster"
  assume_role_policy = data.aws_iam_policy_document.eks_cluster_assume.json
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_kms_key" "eks_secrets" {
  description             = "EKS Kubernetes Secret envelope encryption for ${var.name}"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "eks_secrets" {
  name          = "alias/${var.name}-eks-secrets"
  target_key_id = aws_kms_key.eks_secrets.key_id
}

resource "aws_cloudwatch_log_group" "eks_control_plane" {
  name              = "/aws/eks/${var.name}/cluster"
  retention_in_days = var.log_retention_days
}

resource "aws_eks_cluster" "staging" {
  name                          = var.name
  role_arn                      = aws_iam_role.eks_cluster.arn
  version                       = var.eks_version
  enabled_cluster_log_types     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  deletion_protection           = true
  bootstrap_self_managed_addons = true

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.eks_secrets.arn
    }
  }

  kubernetes_network_config {
    ip_family         = "ipv4"
    service_ipv4_cidr = var.eks_service_ipv4_cidr
  }

  vpc_config {
    subnet_ids              = [for subnet in aws_subnet.app : subnet.id]
    endpoint_private_access = true
    endpoint_public_access  = length(var.eks_public_access_cidrs) > 0
    public_access_cidrs     = var.eks_public_access_cidrs
  }

  depends_on = [
    aws_cloudwatch_log_group.eks_control_plane,
    aws_iam_role_policy_attachment.eks_cluster,
  ]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_eks_access_entry" "administrator" {
  cluster_name      = aws_eks_cluster.staging.name
  principal_arn     = local.eks_admin_principal_arn
  kubernetes_groups = []
  type              = "STANDARD"
}

resource "aws_eks_access_policy_association" "administrator" {
  cluster_name  = aws_eks_cluster.staging.name
  principal_arn = aws_eks_access_entry.administrator.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}

data "aws_iam_policy_document" "eks_node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks_node" {
  name               = "${var.name}-eks-node"
  assume_role_policy = data.aws_iam_policy_document.eks_node_assume.json
}

resource "aws_iam_role_policy_attachment" "eks_node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
  ])
  role       = aws_iam_role.eks_node.name
  policy_arn = each.value
}

resource "aws_launch_template" "eks_node" {
  name_prefix            = "${var.name}-node-"
  update_default_version = true

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      delete_on_termination = true
      encrypted             = true
      volume_size           = var.eks_node_volume_gib
      volume_type           = "gp3"
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_protocol_ipv6          = "disabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${var.name}-node" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.tags, { Name = "${var.name}-node" })
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_eks_node_group" "staging" {
  cluster_name    = aws_eks_cluster.staging.name
  node_group_name = "${var.name}-arm64"
  node_role_arn   = aws_iam_role.eks_node.arn
  subnet_ids      = [for subnet in aws_subnet.app : subnet.id]
  version         = var.eks_version
  ami_type        = "AL2023_ARM_64_STANDARD"
  capacity_type   = "ON_DEMAND"
  instance_types  = var.eks_node_instance_types

  launch_template {
    id      = aws_launch_template.eks_node.id
    version = aws_launch_template.eks_node.latest_version
  }

  scaling_config {
    min_size     = var.eks_node_min_size
    desired_size = var.eks_node_desired_size
    max_size     = var.eks_node_max_size
  }

  update_config {
    max_unavailable = 1
    update_strategy = "DEFAULT"
  }

  node_repair_config {
    enabled = true
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_node,
    aws_vpc_endpoint.interface,
    aws_vpc_endpoint.s3,
  ]

  lifecycle {
    precondition {
      condition = (
        var.eks_node_min_size >= 1 &&
        var.eks_node_desired_size >= var.eks_node_min_size &&
        var.eks_node_max_size >= var.eks_node_desired_size
      )
      error_message = "Require 1 <= node min <= desired <= max."
    }
  }
}

resource "aws_eks_addon" "core" {
  for_each = {
    for name, version in var.eks_addon_versions : name => version
    if name != "aws-secrets-store-csi-driver-provider"
  }

  cluster_name                = aws_eks_cluster.staging.name
  addon_name                  = each.key
  addon_version               = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
  configuration_values = each.key == "vpc-cni" ? jsonencode({
    enableNetworkPolicy = "true"
    nodeAgent = {
      enablePolicyEventLogs = "true"
      logLevel              = "info"
    }
  }) : null

  depends_on = [aws_eks_node_group.staging]
}

resource "aws_eks_addon" "secrets_store" {
  cluster_name                = aws_eks_cluster.staging.name
  addon_name                  = "aws-secrets-store-csi-driver-provider"
  addon_version               = var.eks_addon_versions["aws-secrets-store-csi-driver-provider"]
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
  configuration_values = jsonencode({
    awsRegion = var.aws_region
    secrets-store-csi-driver = {
      enableSecretRotation = true
      rotationPollInterval = "3600s"
    }
  })

  depends_on = [aws_eks_node_group.staging]
}

data "aws_iam_policy_document" "pod_identity_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gateway_workload" {
  name               = "${var.name}-gateway-workload"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
}

data "aws_iam_policy_document" "gateway_workload" {
  statement {
    sid     = "OnlyGatewayRuntimeSecrets"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      for key, secret in aws_secretsmanager_secret.app : secret.arn
      if key != "smoke-client-key"
    ]
  }
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

resource "aws_iam_role_policy" "gateway_workload" {
  role   = aws_iam_role.gateway_workload.id
  policy = data.aws_iam_policy_document.gateway_workload.json
}

resource "aws_eks_pod_identity_association" "gateway" {
  cluster_name    = aws_eks_cluster.staging.name
  namespace       = var.kubernetes_namespace
  service_account = "gateway"
  role_arn        = aws_iam_role.gateway_workload.arn

  depends_on = [
    aws_eks_addon.core["eks-pod-identity-agent"],
    aws_iam_role_policy.gateway_workload,
  ]
}

resource "aws_iam_role" "gateway_smoke" {
  name               = "${var.name}-gateway-smoke"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
}

data "aws_iam_policy_document" "gateway_smoke" {
  statement {
    sid     = "OnlySmokeBootstrapSecrets"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      aws_secretsmanager_secret.app["database-url"].arn,
      aws_secretsmanager_secret.app["smoke-client-key"].arn,
      aws_secretsmanager_secret.app["tls-cert"].arn,
    ]
  }
}

resource "aws_iam_role_policy" "gateway_smoke" {
  role   = aws_iam_role.gateway_smoke.id
  policy = data.aws_iam_policy_document.gateway_smoke.json
}

resource "aws_eks_pod_identity_association" "gateway_smoke" {
  cluster_name    = aws_eks_cluster.staging.name
  namespace       = var.kubernetes_namespace
  service_account = "gateway-smoke"
  role_arn        = aws_iam_role.gateway_smoke.arn

  depends_on = [
    aws_eks_addon.core["eks-pod-identity-agent"],
    aws_iam_role_policy.gateway_smoke,
  ]
}
