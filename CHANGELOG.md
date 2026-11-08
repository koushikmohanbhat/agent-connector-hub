# Changelog

## 0.1.0 (2026-11-08)

First release.

- The hub: one MCP endpoint per profile (`/mcp/<profile>`), MCP 2026-07-28 and 2025-era clients over Streamable
  HTTP; a REST API for calls, approvals, audit, erasure, account linking, administration and health.
- Inbound auth: OIDC (with RFC 9728 protected-resource metadata), API key with end-user headers, dev.
- Identity broker: token exchange (RFC 8693), MCP Enterprise-Managed Authorization (ID-JAG + JWT bearer), linked
  accounts (authorization code + PKCE, encrypted refresh tokens), service accounts, fake; hub-signed user assertions
  with the approver as actor.
- Policies (deny, require approval, rate limits) in the agent-starter-kit language; tool-initiated approvals; held
  calls run with the original user; audit with tokenised arguments; OpenTelemetry spans; on_result / on_audit hooks.
- Packs: salesforce-service (hosted MCP or REST), servicenow-itsm (MCP server or Table API), sap-s4-orders (AWS for
  SAP MCP Server), insurance-core (custom REST template with a mock API), warranty-api (generated from OpenAPI).
- Profile proxies for upstream MCP tools (rename, fixed and hidden arguments, enums).
- Emulators for every pack's system; conformance kit with backend parity; `hub record` for sandbox runs.
- CLI: init, check, dev, serve, tools, call, conformance, record, upstream, generate openapi, new pack, deploy.
- Deploy: Dockerfile, Compose, Terraform for ECS Fargate and Cloud Run; notes for AgentCore Gateway and Agent Registry.
