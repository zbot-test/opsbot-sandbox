terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "opsbot-tfstate-test"
    key          = "sandbox/terraform.tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = "eu-central-1"

  default_tags {
    tags = {
      service = "opsbot-sandbox"
      repo    = "zbot-test/opsbot-sandbox"
    }
  }
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "drift_target" {
  name        = "opsbot-sandbox-drift-target"
  description = "e2e drift scenario target; any ingress rule on it is drift"
  vpc_id      = data.aws_vpc.default.id

  # Omitted, `ingress` is computed and Terraform ignores rules added outside it.
  ingress = []
}
