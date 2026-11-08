# Cloud Run service for the connector-hub image. Ingress is internal by default (agents on Cloud Run, GKE or Agent
# Runtime in the same project / VPC); the service still enforces HUB_AUTH on every call. Environment-specific values
# are variables; secrets are Secret Manager secrets mounted as env vars.
terraform {
  required_version = ">= 1.6"
  required_providers { google = { source = "hashicorp/google", version = ">= 5.30" } }
}

provider "google" {
  project = var.project
  region  = var.region
}

variable "project" { type = string }
variable "region" { type = string }
variable "name" {
  type    = string
  default = "connector-hub"
}
variable "image" { type = string } # Artifact Registry image with tag
variable "environment" {
  type    = map(string)
  default = {}
}
variable "secrets" { # ENV_NAME => Secret Manager secret id (latest version is used)
  type    = map(string)
  default = {}
}
variable "ingress" { # INGRESS_TRAFFIC_INTERNAL_ONLY | INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER | INGRESS_TRAFFIC_ALL
  type    = string
  default = "INGRESS_TRAFFIC_INTERNAL_ONLY"
}
variable "invokers" { # principals allowed to call the service, e.g. the agent's service account
  type    = list(string)
  default = []
}
variable "min_instances" {
  type    = number
  default = 0
}
variable "max_instances" {
  type    = number
  default = 10
}

resource "google_service_account" "hub" {
  account_id   = var.name
  display_name = "connector-hub ${var.name}"
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = var.secrets
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.hub.email}"
}

resource "google_cloud_run_v2_service" "hub" {
  name     = var.name
  location = var.region
  ingress  = var.ingress
  template {
    service_account = google_service_account.hub.email
    timeout         = "120s"
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = var.image
      ports { container_port = 8080 }
      dynamic "env" {
        for_each = merge({ GOOGLE_CLOUD_PROJECT = var.project, HUB_ENV = "prod" }, var.environment)
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.secrets
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
        http_get { path = "/readyz" }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access]
}

# Only the listed principals (e.g. the agent's service account) may invoke the service; HUB_AUTH still applies.
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  for_each = toset(var.invokers)
  name     = google_cloud_run_v2_service.hub.name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

output "url" { value = google_cloud_run_v2_service.hub.uri }
