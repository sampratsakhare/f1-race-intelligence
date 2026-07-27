variable "project_id" {
  description = "GCP project that owns the F1 platform."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "bigquery_location" {
  description = "BigQuery dataset location."
  type        = string
  default     = "US"
}

variable "dataset_id" {
  description = "BigQuery dataset ID."
  type        = string
  default     = "f1_raw"
}

variable "raw_bucket_name" {
  description = "Globally unique Cloud Storage bucket for raw historical JSON."
  type        = string
}

variable "deploy_services" {
  description = "Create Cloud Run services and the authenticated push subscription."
  type        = bool
  default     = false
}

variable "consumer_image" {
  description = "Immutable event-consumer container reference, preferably pinned by digest."
  type        = string
  default     = ""
}

variable "dashboard_image" {
  description = "Immutable dashboard container reference, preferably pinned by digest."
  type        = string
  default     = ""
}

variable "dashboard_public" {
  description = "Allow unauthenticated access to the portfolio dashboard."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect Cloud Run services from accidental deletion."
  type        = bool
  default     = false
}
