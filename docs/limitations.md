# Limitations (v0.1.0)

**Live-tested today: nothing.** Every pack is fixture tested against emulators; none has run against a real
Salesforce org, ServiceNow instance, SAP system or insurance platform.

- **Vendor MCP tool names and shapes are assumptions.** Salesforce hosted MCP servers and ServiceNow MCP servers
  expose tools that depend on configuration; the packs map names in settings, and their argument shapes are
  documented guesses until checked with `hub upstream`. The AWS for SAP MCP Server's argument names and the customer
  return payload must be checked against a deployment.
- **SAP is AWS-only.** The SAP pack proxies the AWS for SAP MCP Server; there is no direct OData backend yet.
- **Identity modes are tested against a fake IdP only.** Real IdPs (Okta, Entra ID, Google, Cognito) differ in token
  exchange support, audiences and claims.
- **Behind AgentCore Gateway, end-user identity does not reach the hub** (the Gateway uses client credentials for MCP
  targets); use service-account packs there, or call the hub directly.
- **Per-process state**: rate limits, the token cache and pending account-link requests live in memory, so they are
  per replica. Held calls, audit and linked accounts are in the shared store.
- **Tools only**: no MCP resources or prompts, no multi-round-trip (in-protocol) approvals; approvals are a held result.
- **Tokenisation** covers structured identifiers (email, Indian mobile numbers, PAN, Aadhaar, cards), not names or
  addresses.
- No Helm chart, no TypeScript SDK, no independent security review yet.
