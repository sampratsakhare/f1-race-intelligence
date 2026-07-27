output "artifact_registry_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.containers.repository_id}"
}

output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "bigquery_dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.f1.dataset_id}"
}

output "pubsub_topic" {
  value = google_pubsub_topic.live_events.name
}

output "consumer_url" {
  value = var.deploy_services ? google_cloud_run_v2_service.consumer[0].uri : null
}

output "dashboard_url" {
  value = var.deploy_services ? google_cloud_run_v2_service.dashboard[0].uri : null
}
