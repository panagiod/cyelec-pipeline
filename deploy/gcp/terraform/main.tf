locals {
  service_name      = "cyelec-${var.environment}"
  firestore_db_name = "cyelec-${var.environment}"
  gcs_bucket_name   = "${var.project_id}-cyelec-${var.environment}-blobs"
  is_prod           = var.environment == "prod"
  admin_secret_id   = "cyelec-prod-admin-key"
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

# Firestore access
resource "google_project_iam_member" "firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
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
# 4. Cloud Run v2 Service (min_instances=0 -> €0.00/mo idle, elastic burst)
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
      max_instance_count = var.max_instances
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
