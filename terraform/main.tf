################################################################################
# AI自律カバレッジ最適化エージェント – Terraform メインインフラ
# Step 1 / Step 4 / Step 7 対応
################################################################################

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  backend "gcs" {
    bucket = "YOUR_TF_STATE_BUCKET"
    prefix = "ai-csv-analyzer/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

###############################################################################
# 1. API 有効化
###############################################################################
resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudbuild.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

###############################################################################
# 2. Cloud Storage – CSV Landing Zone
###############################################################################
resource "google_storage_bucket" "csv_landing" {
  name                        = "${var.project_id}-csv-landing"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 30 }
  }

  versioning { enabled = true }

  depends_on = [google_project_service.apis]
}

###############################################################################
# 3. Cloud SQL – PostgreSQL
###############################################################################
resource "google_sql_database_instance" "main" {
  name             = "${var.project_id}-db"
  database_version = "POSTGRES_15"
  region           = var.region
  deletion_protection = true

  settings {
    tier              = "db-g1-small"
    availability_type = "ZONAL"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
  }

  depends_on = [google_project_service.apis, google_service_networking_connection.private_vpc_conn]
}

resource "google_sql_database" "app_db" {
  name     = "csv_import"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app_user" {
  name     = "app_user"
  instance = google_sql_database_instance.main.name
  password = random_password.db_password.result
}

resource "random_password" "db_password" {
  length  = 32
  special = true
}

###############################################################################
# 4. VPC / Private Service Connection
###############################################################################
resource "google_compute_network" "vpc" {
  name                    = "${var.project_id}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.project_id}-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_global_address" "private_ip_range" {
  name          = "private-ip-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc_conn" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

###############################################################################
# 5. Service Account – Cloud Run 実行用
###############################################################################
resource "google_service_account" "cloud_run_sa" {
  account_id   = "cloud-run-ai-analyzer"
  display_name = "Cloud Run AI Analyzer SA"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "cloud_run_sa_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/cloudsql.client",
    "roles/storage.objectViewer",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

###############################################################################
# 6. Secret Manager
###############################################################################

# Slack Webhook URL (Step 4)
resource "google_secret_manager_secret" "slack_webhook" {
  secret_id = "slack-webhook-url"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "slack_webhook_accessor" {
  secret_id = google_secret_manager_secret.slack_webhook.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# GitHub PAT (Step 7)
resource "google_secret_manager_secret" "github_pat" {
  secret_id = "github-pat"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "github_pat_accessor" {
  secret_id = google_secret_manager_secret.github_pat.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# DB Password
resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password_version" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}

resource "google_secret_manager_secret_iam_member" "db_password_accessor" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

###############################################################################
# 7. Cloud Run サービス
###############################################################################
resource "google_cloud_run_v2_service" "ai_analyzer" {
  name     = "ai-csv-analyzer"
  location = var.region

  template {
    service_account = google_service_account.cloud_run_sa.email

    timeout = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/ai-analyzer/app:latest"

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "DB_INSTANCE"
        value = google_sql_database_instance.main.connection_name
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.csv_landing.name
      }
      env {
        name  = "GITHUB_REPO"
        value = var.github_repo
      }
      env {
        name = "SLACK_WEBHOOK_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.slack_webhook.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GITHUB_PAT"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.github_pat.secret_id
            version = "latest"
          }
        }
      }
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.subnet.id
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.cloud_run_sa_roles,
  ]
}

###############################################################################
# 8. Workload Identity Federation – GitHub Actions 連携 (Step 1)
###############################################################################
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions Pool"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC Provider"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "assertion.repository == '${var.github_repo}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "github_actions_sa" {
  account_id   = "github-actions-deployer"
  display_name = "GitHub Actions Deployer SA"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account_iam_member" "github_wif_binding" {
  service_account_id = google_service_account.github_actions_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

resource "google_project_iam_member" "github_sa_roles" {
  for_each = toset([
    "roles/run.admin",
    "roles/storage.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_actions_sa.email}"
}

###############################################################################
# 9. Artifact Registry
###############################################################################
resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "ai-analyzer"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}
