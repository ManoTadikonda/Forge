terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State contains resource metadata and must be stored remotely, encrypted and
  # locked. Configure with `terraform init -backend-config=...`; no bucket name is
  # hardcoded here so the configuration is not tied to one account.
  backend "s3" {}
}
