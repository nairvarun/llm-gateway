# EKS Auto Mode: AWS runs the nodes (Karpenter-based), the VPC CNI, CoreDNS, kube-proxy, the
# EBS CSI driver and the load balancer controller. Compute, block storage and load balancing
# must all be enabled together, and self-managed add-ons must not be bootstrapped.

resource "aws_cloudwatch_log_group" "cluster" {
  # Created up front so retention is ours, not "never expire".
  name              = "/aws/eks/${var.name}/cluster"
  retention_in_days = var.log_retention_days
}

resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.kubernetes_version
  role_arn = aws_iam_role.cluster.arn

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  bootstrap_self_managed_addons = false

  compute_config {
    enabled       = true
    node_pools    = ["general-purpose", "system"]
    node_role_arn = aws_iam_role.node.arn
  }

  kubernetes_network_config {
    elastic_load_balancing {
      enabled = true
    }
  }

  storage_config {
    block_storage {
      enabled = true
    }
  }

  vpc_config {
    subnet_ids              = module.vpc.private_subnets
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.api_public_access_cidrs
  }

  # STANDARD: EKS upgrades the cluster at the end of standard support instead of moving it
  # into (paid) extended support.
  upgrade_policy {
    support_type = "STANDARD"
  }

  enabled_cluster_log_types = var.control_plane_log_types
  deletion_protection       = var.deletion_protection

  # IAM must exist before the cluster and be deleted after it, or EKS cannot clean up the
  # EC2 resources it created (instances, security groups, load balancers).
  depends_on = [
    aws_iam_role_policy_attachment.cluster,
    aws_iam_role_policy_attachment.node,
    aws_cloudwatch_log_group.cluster,
  ]
}

resource "aws_eks_access_entry" "admin" {
  for_each      = toset(var.admin_principal_arns)
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value
}

resource "aws_eks_access_policy_association" "admin" {
  for_each      = toset(var.admin_principal_arns)
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.admin]
}
