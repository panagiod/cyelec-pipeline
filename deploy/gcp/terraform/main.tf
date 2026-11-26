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
  project                     = var.project_id
  name                        = local.firestore_db_name
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
  delete_protection_state     = local.is_prod ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
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
    enabled = false
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

# ----------------------------------------------------------------------
# 4. Cloud Run v2 Service (min_instances=0 -> €0.00/mo idle, tight burst cap)
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
      min_instance_count = 0
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
# 5. Layer-2 Automated €10/mo Budget Hard Gate (prod-managed, least-privilege)
#    Billing Budget -> Pub/Sub -> cyelec-budget-gate Cloud Run (OIDC auth)
#    Pauses cyelec-staging & cyelec-prod ingress at 90% (€9) safely!
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
