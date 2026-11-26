data "google_project" "current" {
  project_id = var.project_id
}

locals {
  service_name            = "cyelec-${var.environment}"
  firestore_db_name       = "cyelec-${var.environment}"
  gcs_bucket_name         = "${var.project_id}-cyelec-${var.environment}-blobs"
  is_prod                 = var.environment == "prod"
  admin_secret_id         = "cyelec-prod-admin-key"
  effective_max_instances = var.max_instances != null ? var.max_instances : (local.is_prod ? 4 : 2)
  effective_rate_limit    = var.rate_limit_rpm != null ? var.rate_limit_rpm : (local.is_prod ? 120 : 30)
}

# ----------------------------------------------------------------------
# 1. Dedicated Named Firestore Native Database (europe-west1, €0 idle)
# ----------------------------------------------------------------------
resource "google_firestore_database" "workspace" {
  project                           = var.project_id
  name                              = local.firestore_db_name
  location_id                       = var.region
  type                              = "FIRESTORE_NATIVE"
  concurrency_mode                  = "OPTIMISTIC"
  app_engine_integration_mode       = "DISABLED"
  delete_protection_state           = local.is_prod ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  point_in_time_recovery_enablement = local.is_prod && var.enable_pitr ? "POINT_IN_TIME_RECOVERY_ENABLED" : "POINT_IN_TIME_RECOVERY_DISABLED"
}

# ----------------------------------------------------------------------
# 2. Dedicated EU GCS Bucket for Gzipped Phase2DesignResult Snapshots
# ----------------------------------------------------------------------
resource "google_storage_bucket" "revision_blobs" {
  project                     = var.project_id
  name                        = local.gcs_bucket_name
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !local.is_prod

  versioning {
    enabled = local.is_prod
  }

  dynamic "lifecycle_rule" {
    for_each = local.is_prod ? [1] : []
    content {
      condition {
        days_since_noncurrent_time = 30
        num_newer_versions         = 5
      }
      action {
        type = "Delete"
      }
    }
  }

  labels = {
    app         = "cyelec-pipeline"
    environment = var.environment
    managed_by  = "terraform"
  }
}

# ----------------------------------------------------------------------
# 3. Least-Privilege Cloud Run Runtime Service Account
# ----------------------------------------------------------------------
resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "cyelec-${var.environment}-sa"
  display_name = "cyelec-pipeline ${var.environment} Cloud Run runtime SA"
}

# Firestore access strictly scoped to THIS environment's named database
# (staging public demo SA cannot read or write the cyelec-prod database)
resource "google_project_iam_member" "firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"

  condition {
    title       = "firestore_db_${var.environment}_only"
    description = "Restrict Firestore access exclusively to ${google_firestore_database.workspace.name}"
    expression  = "resource.name == \"projects/${var.project_id}/databases/${google_firestore_database.workspace.name}\" || resource.name.startsWith(\"projects/${var.project_id}/databases/${google_firestore_database.workspace.name}/\")"
  }
}

# Scoped strictly to THIS environment's GCS bucket (staging SA cannot touch prod bucket)
resource "google_storage_bucket_iam_member" "blobs_admin" {
  bucket = google_storage_bucket.revision_blobs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# Prod-only Secret Manager access for CYELEC_ADMIN_KEY (populated by bootstrap_wif.sh)
data "google_secret_manager_secret" "prod_admin_key" {
  count     = local.is_prod ? 1 : 0
  project   = var.project_id
  secret_id = local.admin_secret_id
}

resource "google_secret_manager_secret_iam_member" "prod_admin_key_accessor" {
  count     = local.is_prod ? 1 : 0
  project   = var.project_id
  secret_id = data.google_secret_manager_secret.prod_admin_key[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# Optional Commercial GA Secret Manager bindings (Stripe API key, Stripe webhook secret, dedicated JWT key)
locals {
  optional_secret_bindings = local.is_prod ? merge(
    var.stripe_secret_id != "" ? { "STRIPE_SECRET_KEY" = var.stripe_secret_id } : {},
    var.stripe_webhook_secret_id != "" ? { "STRIPE_WEBHOOK_SECRET" = var.stripe_webhook_secret_id } : {},
    var.jwt_secret_id != "" ? { "CYELEC_JWT_SECRET" = var.jwt_secret_id } : {},
  ) : {}

  optional_plain_envs = merge(
    var.google_client_id != "" ? { "CYELEC_GOOGLE_CLIENT_ID" = var.google_client_id } : {},
    var.stripe_price_pilot != "" ? { "STRIPE_PRICE_PILOT" = var.stripe_price_pilot } : {},
    var.stripe_price_pro != "" ? { "STRIPE_PRICE_PRO" = var.stripe_price_pro } : {},
    var.stripe_price_enterprise != "" ? { "STRIPE_PRICE_ENTERPRISE" = var.stripe_price_enterprise } : {},
    local.is_prod && var.pilot_invite_code != "" ? { "CYELEC_PILOT_INVITE_CODE" = var.pilot_invite_code } : {},
  )
}

resource "google_secret_manager_secret_iam_member" "optional_secret_accessor" {
  for_each  = local.optional_secret_bindings
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# ----------------------------------------------------------------------
# 4. Cloud Run v2 Service (min_instances=0 default -> €0.00/mo idle; prod_min_instances=1 for Commercial GA)
# ----------------------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  project  = var.project_id
  name     = local.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "120s"
    max_instance_request_concurrency = var.concurrency

    scaling {
      min_instance_count = local.is_prod ? var.prod_min_instances : 0
      max_instance_count = local.effective_max_instances
    }

    containers {
      image = var.image_uri

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "CYELEC_STORE_BACKEND"
        value = "firestore"
      }
      env {
        name  = "CYELEC_GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "CYELEC_FIRESTORE_DATABASE"
        value = google_firestore_database.workspace.name
      }
      env {
        name  = "CYELEC_GCS_BUCKET"
        value = google_storage_bucket.revision_blobs.name
      }
      env {
        name  = "CYELEC_AUTH_REQUIRED"
        value = local.is_prod ? "1" : "0"
      }
      env {
        name  = "CYELEC_DISABLE_OPENAPI"
        value = local.is_prod ? "1" : "0"
      }
      env {
        name  = "CYELEC_DEMO_LOCK_TENANT_MUTATIONS"
        value = local.is_prod ? "0" : "1"
      }
      env {
        name  = "CYELEC_RATE_LIMIT_RPM"
        value = tostring(local.effective_rate_limit)
      }
      env {
        name  = "CYELEC_ENFORCE_QUOTAS"
        value = "1"
      }

      dynamic "env" {
        for_each = local.optional_plain_envs
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.is_prod ? [1] : []
        content {
          name = "CYELEC_ADMIN_KEY"
          value_source {
            secret_key_ref {
              secret  = data.google_secret_manager_secret.prod_admin_key[0].secret_id
              version = "latest"
            }
          }
        }
      }

      dynamic "env" {
        for_each = local.optional_secret_bindings
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 2
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 12
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        timeout_seconds   = 5
        period_seconds    = 30
        failure_threshold = 3
      }
    }
  }

  depends_on = [
    google_project_iam_member.firestore_user,
    google_storage_bucket_iam_member.blobs_admin,
    google_secret_manager_secret_iam_member.prod_admin_key_accessor,
    google_secret_manager_secret_iam_member.optional_secret_accessor,
  ]
}

# Allow public HTTPS access to Workbench UI & API (prod /v1/* protected by CYELEC_AUTH_REQUIRED=1)
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ----------------------------------------------------------------------
# 5. Layer-2 Automated €10/mo Budget Gate (prod-managed, least-privilege)
#    Billing Budget -> Pub/Sub -> cyelec-budget-gate Cloud Run (OIDC auth)
#    mode='pause_ingress' (€10 hard cap) or 'alert_only' (Commercial GA)
# ----------------------------------------------------------------------
resource "google_pubsub_topic" "budget_gate" {
  count   = local.is_prod ? 1 : 0
  project = var.project_id
  name    = "cyelec-budget-gate"
}

resource "google_service_account" "budget_gate" {
  count        = local.is_prod ? 1 : 0
  project      = var.project_id
  account_id   = "cyelec-budget-gate-sa"
  display_name = "cyelec-pipeline €10 Budget Hard-Gate SA"
}

# Least-privilege: grant roles/run.developer strictly on cyelec-staging & cyelec-prod
# (eliminates project-wide roles/run.admin & deployer SA privilege escalation)
resource "google_cloud_run_v2_service_iam_member" "budget_gate_run_dev" {
  for_each = local.is_prod ? toset(["cyelec-staging", "cyelec-prod"]) : toset([])
  project  = var.project_id
  location = var.region
  name     = each.key
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.budget_gate[0].email}"

  depends_on = [google_cloud_run_v2_service.api]
}

# Least-privilege: allow actAs ONLY on the staging & prod Cloud Run runtime SAs
# (cannot actAs cyelec-gh-deployer or any other project service account)
resource "google_service_account_iam_member" "budget_gate_sa_user" {
  for_each           = local.is_prod ? toset(["cyelec-staging-sa", "cyelec-prod-sa"]) : toset([])
  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.key}@${var.project_id}.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.budget_gate[0].email}"

  depends_on = [google_service_account.runtime]
}

resource "google_service_account" "pubsub_invoker" {
  count        = local.is_prod ? 1 : 0
  project      = var.project_id
  account_id   = "cyelec-pubsub-invoker"
  display_name = "Pub/Sub OIDC Invoker for cyelec-budget-gate"
}

resource "google_service_account_iam_member" "pubsub_token_creator" {
  count              = local.is_prod ? 1 : 0
  service_account_id = google_service_account.pubsub_invoker[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_cloud_run_v2_service" "budget_gate" {
  count    = local.is_prod ? 1 : 0
  project  = var.project_id
  name     = "cyelec-budget-gate"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.budget_gate[0].email
    timeout                          = "60s"
    max_instance_request_concurrency = 4

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.image_uri
      args  = ["budget-gate", "--host", "0.0.0.0"]

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      env {
        name  = "CYELEC_GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "CYELEC_GCP_REGION"
        value = var.region
      }
      env {
        name  = "CYELEC_GATE_TARGET_SERVICES"
        value = "cyelec-staging,cyelec-prod"
      }
      env {
        name  = "CYELEC_BUDGET_GATE_RATIO"
        value = var.budget_gate_ratio
      }
      env {
        name  = "CYELEC_BUDGET_GATE_MODE"
        value = var.budget_gate_mode
      }
      env {
        name  = "CYELEC_EXPECTED_SUBSCRIPTION"
        value = "projects/${var.project_id}/subscriptions/cyelec-budget-gate-push"
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 1
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 10
      }
    }
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.budget_gate_run_dev,
    google_service_account_iam_member.budget_gate_sa_user,
  ]
}

# Locked exclusively to Pub/Sub OIDC invoker SA (public internet gets HTTP 403)
resource "google_cloud_run_v2_service_iam_member" "budget_gate_invoker" {
  count    = local.is_prod ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.budget_gate[0].location
  name     = google_cloud_run_v2_service.budget_gate[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_invoker[0].email}"
}

resource "google_pubsub_subscription" "budget_gate_push" {
  count                = local.is_prod ? 1 : 0
  project              = var.project_id
  name                 = "cyelec-budget-gate-push"
  topic                = google_pubsub_topic.budget_gate[0].id
  ack_deadline_seconds = 30

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.budget_gate[0].uri}/pubsub/budget"
    oidc_token {
      service_account_email = google_service_account.pubsub_invoker[0].email
    }
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.budget_gate_invoker,
    google_service_account_iam_member.pubsub_token_creator,
  ]
}

# ----------------------------------------------------------------------
# 6. Optional Commercial Custom Domain Mapping (prod only, var.custom_domain)
# ----------------------------------------------------------------------
resource "google_cloud_run_domain_mapping" "custom" {
  count    = local.is_prod && var.custom_domain != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = var.custom_domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.api.name
  }
}

# ----------------------------------------------------------------------
# 7. Optional Cloud Monitoring HTTPS Uptime Check & Ops Alert Policy (prod only)
#    Enable via CYELEC_ALERT_EMAIL repo var (requires monitoring.googleapis.com
#    and roles/monitoring.editor on cyelec-gh-deployer from bootstrap_wif.sh)
# ----------------------------------------------------------------------
resource "google_monitoring_uptime_check_config" "prod_health" {
  count        = local.is_prod && (var.enable_monitoring || var.alert_email != "") ? 1 : 0
  project      = var.project_id
  display_name = "CyElec Prod /health HTTPS Uptime"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimprefix(google_cloud_run_v2_service.api.uri, "https://")
    }
  }

  content_matchers {
    content = "\"status\":\"ok\""
    matcher = "CONTAINS_STRING"
  }
}

resource "google_monitoring_notification_channel" "ops_email" {
  count        = local.is_prod && var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "CyElec Production Ops Email"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_alert_policy" "prod_uptime" {
  count                 = local.is_prod && (var.enable_monitoring || var.alert_email != "") ? 1 : 0
  project               = var.project_id
  display_name          = "CyElec Prod Uptime Check Failed"
  combiner              = "OR"
  notification_channels = var.alert_email != "" ? [google_monitoring_notification_channel.ops_email[0].name] : []

  conditions {
    display_name = "Prod /health HTTPS check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.labels.check_id=\"${google_monitoring_uptime_check_config.prod_health[0].uptime_check_id}\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 1

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }

      trigger {
        count = 1
      }
    }
  }
}

