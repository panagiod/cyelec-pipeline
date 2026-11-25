terraform {
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
  }

  # Remote EU GCS backend configured at `terraform init -backend-config=...`
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
