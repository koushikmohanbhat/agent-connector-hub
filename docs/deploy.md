# Deploy

| Target | How |
|---|---|
| Local | `hub dev`; or `docker compose up` (hub on Postgres, plus the insurance mock API on the network) |
| AWS | ECS Fargate behind an internal ALB: [`deploy/terraform/aws-ecs`](../deploy/terraform/aws-ecs/README.md) |
| AWS, with AgentCore Gateway | the hub as an MCP server target: [`deploy/agentcore-gateway`](../deploy/agentcore-gateway/README.md) |
| Google Cloud | Cloud Run, internal ingress: [`deploy/terraform/gcp-cloud-run`](../deploy/terraform/gcp-cloud-run/README.md) |
| Google Cloud, with Agent Registry | register the hub's profiles: [`deploy/agent-registry`](../deploy/agent-registry/README.md) |
| Kubernetes / on-prem | the same image; set the environment and a Postgres URL |

The image runs `hub serve`. Production checklist: `HUB_ENV=prod`, `HUB_AUTH=oidc` (or `api_key` behind a trusted
backend), a Postgres `HUB_STORE`, `HUB_TOKEN_KEY` from a secret manager, every pack off `fake`, `HUB_PUBLIC_URL`, and
`HUB_ALLOWED_HOSTS` if the hub is reachable by browsers. `hub deploy --print <target>` prints the target's guide.

Replicas share held calls, audit and linked accounts through the store; rate limits, the token cache and pending
account-link requests are per process (see [limitations](limitations.md)).
