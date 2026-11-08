# Deploy to Google Cloud: Cloud Run

1. Build and push the image to Artifact Registry:
   ```bash
   docker build -t <region>-docker.pkg.dev/<project>/<repo>/connector-hub:v1 --build-arg EXTRAS=postgres,otel .
   docker push <region>-docker.pkg.dev/<project>/<repo>/connector-hub:v1
   ```
2. Create secrets in Secret Manager: `HUB_STORE` (Cloud SQL / AlloyDB Postgres URL), `HUB_TOKEN_KEY`, and each
   system's client secret.
3. `cd deploy/terraform/gcp-cloud-run && cp terraform.tfvars.example terraform.tfvars`, fill it in, then
   `terraform init && terraform plan && terraform apply`.
4. Grant the agent's service account `roles/run.invoker` through `invokers`, and call `<url>/mcp/<profile>`.

Ingress is internal-only by default. To govern egress centrally, register the hub in Agent Registry and route agents
through Agent Gateway (see `deploy/agent-registry`). The SAP pack only works on AWS (it proxies the AWS for SAP MCP
Server); on Google Cloud, leave it out of your profiles or run it on its fake.

Status: implemented, checked with `terraform validate` in CI; not yet applied in a live project.
