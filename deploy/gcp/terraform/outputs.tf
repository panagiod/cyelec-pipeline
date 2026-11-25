output "service_url" {
  description = "Live HTTPS URL of the deployed Cloud Run service."
  value       = google_cloud_run_v2_service.api.uri
}

output "firestore_database" {
  description = "Named Firestore Native database in europe-west1."
  value       = google_firestore_database.workspace.name
}

output "gcs_bucket" {
  description = "EU GCS bucket storing gzipped revision JSON snapshots."
  value       = google_storage_bucket.revision_blobs.name
}

output "service_account_email" {
  description = "Least-privilege Cloud Run runtime service account."
  value       = google_service_account.runtime.email
}
