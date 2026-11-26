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

variable "prod_min_instances" {
  description = "Minimum warm Cloud Run instances for prod (0 = €0 idle Pilot cap; set 1 for zero-cold-start Commercial GA)."
  type        = number
  default     = 0
}

variable "budget_gate_mode" {
  description = "Budget gate behavior at threshold: 'pause_ingress' (€10 hard cap) or 'alert_only' (Commercial GA)."
  type        = string
  default     = "pause_ingress"

  validation {
    condition     = contains(["pause_ingress", "alert_only"], var.budget_gate_mode)
    error_message = "budget_gate_mode must be 'pause_ingress' or 'alert_only'."
  }
}

variable "enable_pitr" {
  description = "Enable 7-day continuous Firestore Point-in-Time Recovery (PITR) on prod database."
  type        = bool
  default     = true
}

variable "enable_monitoring" {
  description = "Enable Cloud Monitoring HTTPS uptime check on prod /health (requires monitoring.googleapis.com)."
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "Optional ops email address for Cloud Monitoring uptime & error alerts on prod."
  type        = string
  default     = ""
}

variable "custom_domain" {
  description = "Optional verified custom domain (e.g. app.cyelec.cy) mapped to cyelec-prod."
  type        = string
  default     = ""
}

variable "google_client_id" {
  description = "Optional Google OAuth 2.0 Client ID for firm Google OIDC SSO (CYELEC_GOOGLE_CLIENT_ID)."
  type        = string
  default     = ""
}

variable "stripe_secret_id" {
  description = "Optional Secret Manager secret ID containing STRIPE_SECRET_KEY (prod only)."
  type        = string
  default     = ""
}

variable "stripe_webhook_secret_id" {
  description = "Optional Secret Manager secret ID containing STRIPE_WEBHOOK_SECRET (prod only)."
  type        = string
  default     = ""
}

variable "stripe_price_pilot" {
  description = "Optional Stripe Price ID for the Cyprus Solo Pilot tier (STRIPE_PRICE_PILOT)."
  type        = string
  default     = ""
}

variable "stripe_price_pro" {
  description = "Optional Stripe Price ID for the Cyprus MEP Firm Pro tier (STRIPE_PRICE_PRO)."
  type        = string
  default     = ""
}

variable "stripe_price_enterprise" {
  description = "Optional Stripe Price ID for the Enterprise Multi-Office tier (STRIPE_PRICE_ENTERPRISE)."
  type        = string
  default     = ""
}

variable "jwt_secret_id" {
  description = "Optional dedicated Secret Manager secret ID for CYELEC_JWT_SECRET (falls back to HMAC derivation from CYELEC_ADMIN_KEY)."
  type        = string
  default     = ""
}

variable "pilot_invite_code" {
  description = "Optional closed-pilot registration invite code on prod (auto-grants pilot tier: 100 solves/mo, 3 seats)."
  type        = string
  default     = ""
  sensitive   = true
}


