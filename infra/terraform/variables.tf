variable "expected_account_id" {
  description = "Account guard; a plan in another AWS account must fail."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Supply a 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "Reviewed staging region."
  type        = string
  default     = "ap-south-1"
}

variable "name" {
  description = "Short prefix for isolated staging resources."
  type        = string
  default     = "llm-gateway-staging"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.name))
    error_message = "Use 3–25 lowercase letters, digits, or hyphens."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.86.0.0/16"
}

variable "eks_version" {
  description = "Pinned EKS Kubernetes minor version verified in the reviewed region."
  type        = string
  default     = "1.35"
  validation {
    condition     = can(regex("^1\\.[0-9]{2}$", var.eks_version))
    error_message = "Supply an EKS minor version such as 1.35."
  }
}

variable "eks_addon_versions" {
  description = "Pinned add-on versions discovered for the selected EKS version and region."
  type        = map(string)
  default = {
    coredns                               = "v1.13.2-eksbuild.31"
    eks-pod-identity-agent                = "v1.3.10-eksbuild.3"
    kube-proxy                            = "v1.35.3-eksbuild.29"
    vpc-cni                               = "v1.22.4-eksbuild.3"
    aws-secrets-store-csi-driver-provider = "v3.1.3-eksbuild.1"
  }
  validation {
    condition = length(setsubtract(toset(keys(var.eks_addon_versions)), toset([
      "coredns",
      "eks-pod-identity-agent",
      "kube-proxy",
      "vpc-cni",
      "aws-secrets-store-csi-driver-provider",
      ]))) == 0 && length(setsubtract(toset([
      "coredns",
      "eks-pod-identity-agent",
      "kube-proxy",
      "vpc-cni",
      "aws-secrets-store-csi-driver-provider",
    ]), toset(keys(var.eks_addon_versions)))) == 0 && alltrue([for version in values(var.eks_addon_versions) : length(version) > 0])
    error_message = "Pin exactly the required five EKS add-ons to non-empty versions."
  }
}

variable "eks_admin_principal_arn" {
  description = "IAM principal receiving the initial EKS access entry; null uses the planning caller."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = var.eks_admin_principal_arn == null || can(regex(
      "^arn:aws:iam::[0-9]{12}:(user|role)/.+$",
      var.eks_admin_principal_arn
    ))
    error_message = "Supply an IAM user/role ARN or null."
  }
}

variable "eks_public_access_cidrs" {
  description = "Optional reviewed administrator CIDRs; empty keeps the Kubernetes API private-only."
  type        = list(string)
  default     = []
  validation {
    condition = alltrue([
      for cidr in var.eks_public_access_cidrs :
      can(cidrnetmask(cidr)) && !contains(["0.0.0.0/0", "::/0"], cidr)
    ])
    error_message = "Use valid bounded administrator CIDRs; world-open access is prohibited."
  }
}

variable "eks_service_ipv4_cidr" {
  description = "Kubernetes Service IPv4 range; must not overlap the VPC or connected networks."
  type        = string
  default     = "10.87.0.0/16"
}

variable "eks_node_instance_types" {
  description = "Reviewed ARM64 managed-node instance types."
  type        = list(string)
  default     = ["t4g.medium"]
  validation {
    condition     = length(var.eks_node_instance_types) > 0
    error_message = "Supply at least one managed-node instance type."
  }
}

variable "eks_node_min_size" {
  type    = number
  default = 2
}

variable "eks_node_desired_size" {
  type    = number
  default = 2
}

variable "eks_node_max_size" {
  type    = number
  default = 3
}

variable "eks_node_volume_gib" {
  type    = number
  default = 30
  validation {
    condition     = var.eks_node_volume_gib >= 20 && var.eks_node_volume_gib <= 100
    error_message = "Node root volume must be 20–100 GiB."
  }
}

variable "kubernetes_namespace" {
  type    = string
  default = "llm-gateway"
  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.kubernetes_namespace))
    error_message = "Use a valid lowercase Kubernetes namespace."
  }
}

variable "monthly_budget_usd" {
  description = "Provisional AWS Budget alert, not an enforced spending ceiling."
  type        = number
  default     = 100
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "The monthly budget alert must be positive."
  }
}

variable "budget_alert_email" {
  description = "Owner-controlled budget notification destination; null creates no email notification."
  type        = string
  default     = null
  nullable    = true
}

variable "log_retention_days" {
  type    = number
  default = 7
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "Use a supported bounded CloudWatch retention period."
  }
}

variable "artifact_retention_days" {
  type    = number
  default = 30
  validation {
    condition     = var.artifact_retention_days >= 1 && var.artifact_retention_days <= 90
    error_message = "Artifact retention must be 1–90 days."
  }
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
  validation {
    condition     = var.db_backup_retention_days >= 1 && var.db_backup_retention_days <= 35
    error_message = "RDS backup retention must be 1–35 days."
  }
}
