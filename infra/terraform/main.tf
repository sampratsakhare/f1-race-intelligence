locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
  ])
  labels = {
    application = "f1-race-intelligence"
    managed_by  = "terraform"
  }
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "apis" {
  for_each = local.services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "f1" {
  dataset_id                 = var.dataset_id
  location                   = var.bigquery_location
  delete_contents_on_destroy = false
  labels                     = local.labels

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket" "raw" {
  name                        = var.raw_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = "f1-race-intelligence"
  description   = "Container images for F1 Race Intelligence"
  format        = "DOCKER"
  labels        = local.labels

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "live_events" {
  name   = "f1-live-events"
  labels = local.labels

  message_retention_duration = "86400s"
  depends_on                 = [google_project_service.apis]
}

resource "google_pubsub_topic" "dead_letter" {
  name   = "f1-live-events-dead-letter"
  labels = local.labels

  message_retention_duration = "604800s"
  depends_on                 = [google_project_service.apis]
}

resource "google_pubsub_subscription" "dead_letter" {
  name  = "f1-live-events-dead-letter-inspection"
  topic = google_pubsub_topic.dead_letter.id

  ack_deadline_seconds       = 30
  message_retention_duration = "604800s"
  expiration_policy {
    ttl = ""
  }
}

resource "google_service_account" "consumer" {
  account_id   = "f1-event-consumer"
  display_name = "F1 event consumer"
}

resource "google_service_account" "dashboard" {
  account_id   = "f1-dashboard"
  display_name = "F1 dashboard"
}

resource "google_service_account" "pubsub_push" {
  account_id   = "f1-pubsub-push"
  display_name = "F1 Pub/Sub push identity"
}

resource "google_bigquery_dataset_iam_member" "consumer_editor" {
  dataset_id = google_bigquery_dataset.f1.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.consumer.email}"
}

resource "google_bigquery_dataset_iam_member" "dashboard_viewer" {
  dataset_id = google_bigquery_dataset.f1.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.dashboard.email}"
}

resource "google_project_iam_member" "dashboard_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dashboard.email}"
}

resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"

  depends_on = [google_project_service.apis["pubsub.googleapis.com"]]
}

resource "google_cloud_run_v2_service" "consumer" {
  count = var.deploy_services ? 1 : 0

  name                = "f1-event-consumer"
  location            = var.region
  deletion_protection = var.deletion_protection
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.consumer.email
    timeout         = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image = var.consumer_image

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "BQ_DATASET"
        value = google_bigquery_dataset.f1.dataset_id
      }
      env {
        name  = "BQ_LOCATION"
        value = var.bigquery_location
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.consumer_image != ""
      error_message = "consumer_image is required when deploy_services is true."
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "dashboard" {
  count = var.deploy_services ? 1 : 0

  name                = "f1-race-dashboard"
  location            = var.region
  deletion_protection = var.deletion_protection
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.dashboard.email
    timeout         = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.dashboard_image

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "BQ_DATASET"
        value = google_bigquery_dataset.f1.dataset_id
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.dashboard_image != ""
      error_message = "dashboard_image is required when deploy_services is true."
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "consumer_invoker" {
  count = var.deploy_services ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.consumer[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_push.email}"
}

resource "google_cloud_run_v2_service_iam_member" "dashboard_public" {
  count = var.deploy_services && var.dashboard_public ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.dashboard[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_pubsub_subscription" "live_push" {
  count = var.deploy_services ? 1 : 0

  name  = "f1-live-events-consumer"
  topic = google_pubsub_topic.live_events.id

  ack_deadline_seconds       = 30
  message_retention_duration = "604800s"

  push_config {
    push_endpoint = google_cloud_run_v2_service.consumer[0].uri

    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
      audience              = google_cloud_run_v2_service.consumer[0].uri
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.consumer_invoker,
    google_pubsub_topic_iam_member.dead_letter_publisher,
    google_service_account_iam_member.pubsub_token_creator,
  ]
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  role   = "roles/pubsub.publisher"
  topic  = google_pubsub_topic.dead_letter.name
  member = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription_iam_member" "dead_letter_forwarder" {
  count = var.deploy_services ? 1 : 0

  role         = "roles/pubsub.subscriber"
  subscription = google_pubsub_subscription.live_push[0].name
  member       = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
