# Phase 8 one-day burst. Pins are binding (ADR 014). Local state on purpose: the stack lives
# for hours and is destroyed the same day; see ADR 014 for the S3-backend alternative.
terraform {
  required_version = "~> 1.13.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.20.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = local.tags
  }
}
