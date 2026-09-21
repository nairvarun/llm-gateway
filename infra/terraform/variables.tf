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

variable "acm_certificate_arn" {
  description = "Existing ISSUED ACM certificate for the chosen staging hostname; null keeps ingress closed."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = var.acm_certificate_arn == null || can(regex(
      "^arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/[0-9a-f-]+$",
      var.acm_certificate_arn
    ))
    error_message = "Supply an ACM certificate ARN or null."
  }
}

variable "runtime_image_digest" {
  description = "Previously pushed ECR SHA-256 image digest; null creates no task definition or service."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.runtime_image_digest == null || can(regex("^sha256:[0-9a-f]{64}$", var.runtime_image_digest))
    error_message = "Supply an immutable sha256:<64 lowercase hex> digest or null."
  }
}

variable "enable_runtime" {
  description = "Requires owner-approved image, certificate, populated secrets, and a completed migration."
  type        = bool
  default     = false
  validation {
    condition     = !var.enable_runtime || (var.acm_certificate_arn != null && var.runtime_image_digest != null)
    error_message = "Runtime requires an ACM certificate and immutable image digest."
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
