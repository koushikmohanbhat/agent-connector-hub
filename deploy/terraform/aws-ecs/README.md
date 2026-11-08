# Deploy to AWS: ECS Fargate behind an internal ALB

1. Build and push the image:
   ```bash
   docker build -t connector-hub --build-arg EXTRAS=postgres,otel .
   aws ecr create-repository --repository-name connector-hub
   docker tag connector-hub <account>.dkr.ecr.<region>.amazonaws.com/connector-hub:v1 && docker push ...
   ```
2. Create secrets in Secrets Manager: `HUB_STORE` (an RDS / Aurora Postgres URL), `HUB_TOKEN_KEY`, and each system's
   client secret (`SALESFORCE_CLIENT_SECRET`, `SAP_CLIENT_SECRET`, ...).
3. `cd deploy/terraform/aws-ecs && cp terraform.tfvars.example terraform.tfvars`, fill it in, then
   `terraform init && terraform plan && terraform apply`.
4. Check `https://<alb>/readyz` from inside the VPC: it lists each pack and fails if any pack is still on fakes
   (the module sets `HUB_ENV=prod`).
5. Point the agent at `https://<alb>/mcp/<profile>`.

The ALB is internal by default and only `allowed_cidrs` can reach it. The SAP pack reaches the AWS for SAP MCP Server
on AgentCore; give the task's network a route to it. Telemetry: run the ADOT collector as a sidecar or set
`OTEL_EXPORTER_OTLP_ENDPOINT` (with the `otel` extra in the image).

Status: implemented, checked with `terraform validate` in CI; not yet applied in a live account.
