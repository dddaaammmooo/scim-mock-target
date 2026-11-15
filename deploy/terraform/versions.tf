terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.100"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # No backend is configured on purpose (local state by default).
  # See README.md for an example S3 backend block.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Project   = var.name
        ManagedBy = "terraform"
        App       = "scim-target"
      },
      var.tags
    )
  }
}
