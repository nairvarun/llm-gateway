variable "region" {
  description = "AWS region for the cluster, e.g. eu-west-1."
  type        = string
}

variable "name" {
  description = "Name used for the cluster, VPC, IAM roles and ECR repository."
  type        = string
  default     = "llm-gateway"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version. Raise it one minor version at a time to upgrade."
  type        = string
  default     = "1.35"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across (EKS needs at least 2)."
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "az_count must be between 2 and 4."
  }
}

variable "single_nat_gateway" {
  description = "One shared NAT gateway (cheaper) instead of one per AZ (survives an AZ outage)."
  type        = bool
  default     = true
}

variable "api_public_access_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint. Narrow this to your IP."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "admin_principal_arns" {
  description = "Extra IAM users or roles to give cluster-admin. The identity running Terraform gets it automatically."
  type        = list(string)
  default     = []
}

variable "control_plane_log_types" {
  description = "Control plane logs to send to CloudWatch (api, audit, authenticator, controllerManager, scheduler)."
  type        = list(string)
  default     = ["audit", "authenticator"]
}

variable "log_retention_days" {
  description = "Retention for the control plane log group."
  type        = number
  default     = 30
}

variable "deletion_protection" {
  description = "Block cluster deletion until this is turned off. Off by default for a learning cluster."
  type        = bool
  default     = false
}

variable "ecr_force_delete" {
  description = "Allow terraform destroy to delete the ECR repository even if it still holds images."
  type        = bool
  default     = false
}
