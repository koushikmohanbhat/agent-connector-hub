# agent-connector-hub

**Governed enterprise tools for AI agents, over MCP.** One MCP endpoint per agent that fronts the vendors' own MCP
servers (Salesforce, ServiceNow, AWS for SAP) and your own APIs, and adds what agent pilots get stuck on:

- **Per-user identity downstream.** Token exchange (RFC 8693), MCP Enterprise-Managed Authorization (ID-JAG),
  linked accounts, or a service account that filters per user, so each system applies the end user's own permissions.
- **Business tools, not raw APIs.** A few typed, documented actions per system (`open_claim`, `create_return_request`),
  with data rules inside the tool and no free-form SOQL or OData from the model.
- **Policy and approvals.** Deny, require approval and rate limits per tool; held calls run later with the original
  user's identity, with the approver recorded as the actor.
- **Audit.** Every call, with user, tool, decision and outcome; identifiers tokenised; OpenTelemetry spans.
- **Fakes for everything.** Each pack runs offline against an emulator of the system on the same request path,
  so agents, evals and CI work without accounts. A conformance kit checks every pack.

It is catalog item **3.2** of the partner accelerators. It works with any MCP client, and with
[agent-starter-kit](https://github.com/koushikmohanbhat/agent-starter-kit) (`kit add connectors`).

| | |
|---|---|
| Protocol | MCP 2026-07-28 (stateless) and 2025-era clients, Streamable HTTP, MCP Python SDK 2.x |
| Packs | `salesforce-service`, `servicenow-itsm`, `sap-s4-orders`, `insurance-core`, `warranty-api` (generated from OpenAPI) |
| Auth into the hub | OIDC (the user's token), API key + end-user header (trusted agent backend), dev |
| Stores | SQLite, Postgres |
| Deploy | Docker, Compose, Terraform for ECS Fargate and Cloud Run; notes for AgentCore Gateway and Agent Registry |
| Python | 3.11+ |
| Status | v0.1.0. All packs fixture tested offline; **nothing live tested yet** (see [limitations](docs/limitations.md)) |

## Quick start (offline, no accounts)

```bash
pip install "agent-connector-hub @ git+https://github.com/koushikmohanbhat/agent-connector-hub"
hub init my-hub && cd my-hub
hub conformance             # every pack against its fakes
hub dev                     # http://127.0.0.1:8000: dev page; MCP at /mcp/<profile>
```

Call a tool from the command line, as a user:

```bash
hub call support-agent get_case --args '{"case_number": "00001001"}' --user cust-001
hub call support-agent update_case_status --args '{"case_number": "00001001", "status": "Closed"}' --user cust-001
#   -> pending_approval: a support lead decides on the dev page or POST /v1/approvals/{id}
```

Point any MCP client at `http://127.0.0.1:8000/mcp/support-agent` (dev mode takes the user from `X-User-Id`).

## How it fits

```
agent (any framework)  --MCP-->  hub /mcp/<profile>  --> policy --> identity broker --> pack tool
                                    |  audit, approvals          (token for this user)    |
                                    v                                                       v
                               Postgres / SQLite          vendor MCP server, REST API, or fake
```

A **profile** is the tool set one agent sees (`profiles/*.yaml`). A **pack** is everything for one system
(`packs/<name>/`: tools, backends, auth, policies, fake, conformance cases, README).

## Docs

- [Getting started](docs/getting-started.md): run, call, connect a real system
- [Concepts](docs/concepts.md): profiles, packs, the call pipeline
- [Identity](docs/identity.md): inbound auth and the five downstream modes
- [Policies, approvals and audit](docs/approvals-and-audit.md)
- [Writing a pack](docs/writing-a-pack.md), and [packs from OpenAPI](docs/openapi.md)
- [Proxying vendor MCP servers](docs/proxy.md)
- [Testing: fakes, conformance, recording](docs/testing.md)
- [Service API](docs/service-api.md)
- [Deploy](docs/deploy.md) · [Security](docs/security.md) · [Integrations](docs/integrations.md) · [Limitations](docs/limitations.md)

## License

Apache-2.0.
