locals {
  tags = {
    Project     = "llm-reliability-gateway"
    Environment = "staging"
    ManagedBy   = "terraform"
  }
  azs = ["${var.aws_region}a", "${var.aws_region}b"]
  public_cidrs = {
    (local.azs[0]) = cidrsubnet(var.vpc_cidr, 8, 0)
    (local.azs[1]) = cidrsubnet(var.vpc_cidr, 8, 1)
  }
  app_cidrs = {
    (local.azs[0]) = cidrsubnet(var.vpc_cidr, 8, 10)
    (local.azs[1]) = cidrsubnet(var.vpc_cidr, 8, 11)
  }
  data_cidrs = {
    (local.azs[0]) = cidrsubnet(var.vpc_cidr, 8, 20)
    (local.azs[1]) = cidrsubnet(var.vpc_cidr, 8, 21)
  }
  endpoints = toset(["ecr.api", "ecr.dkr", "logs", "secretsmanager"])
}

data "aws_caller_identity" "current" {}
