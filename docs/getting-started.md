# Getting started

## 1. Run it offline

```bash
pip install "agent-connector-hub @ git+https://github.com/koushikmohanbhat/agent-connector-hub"
hub init my-hub && cd my-hub
hub check                   # packs, profiles and the tools each profile exposes
hub conformance             # every pack against its fakes
hub dev                     # http://127.0.0.1:8000
```

`hub dev` sets `HUB_ENV=dev` and `HUB_AUTH=dev`: the user comes from the `X-User-Id`, `X-User-Roles` and `X-Tenant`
headers, and every pack runs on its fake. The dev page lists profiles and tools, lets you call a tool as any user and
decide held calls.

Fake users to try: `cust-001` / `cust-002` (Salesforce, SAP, insurance), `emp-001` / `emp-002` (ServiceNow). Roles:
`support_agent`, `support_lead`, `itil`, `service_desk_lead`, `sales_ops`, `claims_handler`, `claims_supervisor`.

## 2. Connect an agent

Any MCP client works. The endpoint is `/mcp/<profile>`, Streamable HTTP. With the agent-starter-kit:

```bash
kit add connectors          # in your kit folder
export CONNECTOR_HUB_URL=http://127.0.0.1:8000 CONNECTOR_HUB_PROFILE=claims-agent CONNECTOR_HUB_API_KEY=...
```

and run the hub with `HUB_AUTH=api_key HUB_API_KEYS=...` so the kit can name the end user.

## 3. Connect a real system

Each pack's README says what to set up in the vendor system and which variables to set. The pattern:

1. Pick the backend (`SALESFORCE_BACKEND=mcp` or `rest`) and point it at the system (`SALESFORCE_MCP_URL`).
2. Pick the identity mode (`SALESFORCE_IDENTITY=token_exchange`) and set its token endpoint and client.
3. For MCP backends, list the upstream tools and map their names: `hub upstream salesforce-service --user <id>`.
4. `hub record salesforce-service --out recorded.jsonl --user <a sandbox user>` runs the pack's read calls against
   the sandbox and saves tokenised results to review.
5. Set `HUB_ENV=prod`: fakes and fake identities are then refused, and `/readyz` fails if anything is still on a fake.

## 4. Deploy

See [deploy](deploy.md): Docker, ECS Fargate or Cloud Run, optionally behind AgentCore Gateway or registered in
Agent Registry.
