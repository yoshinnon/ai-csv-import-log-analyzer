################################################################################
# variables.tf
################################################################################
variable "project_id" {
  description = "GCP プロジェクト ID"
  type        = string
}

variable "region" {
  description = "GCP リージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "github_repo" {
  description = "GitHub リポジトリ (owner/repo 形式)"
  type        = string
}
