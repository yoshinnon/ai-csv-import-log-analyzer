################################################################################
# outputs.tf
################################################################################
output "cloud_run_url" {
  description = "Cloud Run サービス URL"
  value       = google_cloud_run_v2_service.ai_analyzer.uri
}

output "workload_identity_provider" {
  description = "GitHub Actions で使用する Workload Identity Provider"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_actions_sa_email" {
  description = "GitHub Actions が impersonate する SA メール"
  value       = google_service_account.github_actions_sa.email
}

output "csv_bucket_name" {
  description = "CSV Landing Zone バケット名"
  value       = google_storage_bucket.csv_landing.name
}

output "artifact_registry_repo" {
  description = "Artifact Registry リポジトリ URL"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/ai-analyzer"
}
