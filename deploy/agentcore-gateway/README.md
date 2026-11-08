# Behind Amazon Bedrock AgentCore Gateway

AgentCore Gateway can aggregate MCP servers into one endpoint for agents. The hub can be one of its MCP server
targets, so agents that already use the Gateway see the hub's governed tools next to their other targets.

## Steps

1. Deploy the hub (ECS Fargate, `deploy/terraform/aws-ecs`), reachable from the Gateway.
2. Configure the hub with `HUB_AUTH=oidc`, with `HUB_OIDC_ISSUER` / `HUB_OIDC_AUDIENCE` matching the OAuth client the
   Gateway uses for outbound calls (for example a Cognito app client).
3. In AgentCore Gateway, add an MCP server target with URL `https://<hub>/mcp/<profile>` and an OAuth
   (client credentials) outbound credential provider for that client.
4. List the Gateway's tools from your agent: the hub's tools appear under the target.

## What changes for identity

When the Gateway is in front, the hub sees the Gateway's machine-to-machine token, not the end user's. AgentCore
Gateway's outbound auth for MCP server targets uses client credentials, so the end user's identity does not reach
the hub. That suits tools that run on service accounts (for example `servicenow-itsm` with `service_account`), with the
audit log recording the Gateway's client. For per-user identity (Salesforce sharing, SAP principal propagation, a
policyholder's own policies), call the hub directly from the agent, with the user's token (`HUB_AUTH=oidc`) or a
trusted backend key plus `X-End-User-Id` (`HUB_AUTH=api_key`).

Status: documented, not tested against a live Gateway.
