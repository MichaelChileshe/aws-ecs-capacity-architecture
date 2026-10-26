terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.60"
    }
  }

  # Remote state in the shared bucket (one key per project), passed at init time:
  #   terraform init -backend-config="bucket=nkosi-tfstate-<account id>"
  backend "s3" {
    key          = "aws-ecs-capacity-architecture/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "aws-ecs-capacity-architecture"
      ManagedBy = "terraform"
    }
  }
}
