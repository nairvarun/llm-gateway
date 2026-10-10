terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State is local by default. For anything shared, use S3 (native locking, no DynamoDB):
  # backend "s3" {
  #   bucket       = "<your-state-bucket>"
  #   key          = "llm-gateway/eks.tfstate"
  #   region       = "<region>"
  #   use_lockfile = true
  #   encrypt      = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.name
      ManagedBy = "terraform"
    }
  }
}
