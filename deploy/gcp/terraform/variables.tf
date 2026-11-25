variable "project_id" {
  description = "Google Cloud Project ID hosting cyelec-pipeline."
  type        = string
}

variable "region" {
  description = "EU Google Cloud region (default: europe-west1 Belgium, Tier-1 lowest EU pricing)."
  type        = string
  default     = "europe-west1"
}

variable "environment" {
  description = "Target deployment environment: 'staging' (open demo) or 'prod' (strict auth)."
  type        = string
  default     = "staging"

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be either 'staging' or 'prod'."
  }
}

variable "image_uri" {
  description = "Immutable Artifact Registry container image URI (SHA-pinned)."
  type        = string
}

variable "max_instances" {
  description = "Optional Cloud Run max replicas override (defaults: staging=2, prod=4; min is always 0)."
  type        = number
  default     = null
}

variable "rate_limit_rpm" {
  description = "Optional per-client solves/min rate limit (defaults: staging=30, prod=120)."
  type        = number
  default     = null
}

variable "budget_gate_ratio" {
  description = "Spend ratio (e.g. 0.90 = €9 on €10 budget) at which the hard gate pauses public ingress."
  type        = string
  default     = "0.90"
}

variable "cpu_limit" {
  description = "vCPU limit per Cloud Run instance (2 vCPU recommended for DXF + HiGHS MILP)."
  type        = string
  default     = "2"
}

variable "memory_limit" {
  description = "Memory limit per Cloud Run instance."
  type        = string
  default     = "2Gi"
}

variable "concurrency" {
  description = "Max concurrent requests per Cloud Run instance (kept low to avoid Python GIL contention)."
  type        = number
  default     = 4
}
