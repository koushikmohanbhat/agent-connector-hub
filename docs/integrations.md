# Integrations

## agent-starter-kit (3.1)

`kit add connectors` adds an `mcp_tools:` source to `agent.yaml`. At start-up the kit lists the profile's tools from
the hub and binds them like local tools, on every framework the kit supports. Each call forwards the kit's verified
user (`X-End-User-Id`, `X-End-User-Roles`, `X-Tenant`) with the kit's API key, so run the hub with
`HUB_AUTH=api_key`. The kit's own policies apply first; the hub's apply on top; a hub hold reaches the model as
`pending_approval`.

## GenAI Eval Framework (4.1)

Run the hub on fakes and point the agent under test at it. Tool-agent suites can then check final state in the fakes
(for example a claim registered, a return held for approval), with `/v1/dev/reset` between runs.

## DPDP Data Rights Framework (5.5)

Use the hooks in `hub.yaml`:
- `on_result`: tokenise or redact personal data in tool results with the framework's detectors before the model sees
  them, and register lineage for what was read.
- `on_audit`: forward audit rows to the framework's processing record.
- `DELETE /v1/users/{id}` is the hub's erasure connector.
