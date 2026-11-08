# Concepts

## Profiles

A profile is one MCP endpoint, `/mcp/<name>`, with the tools one agent may see:

```yaml
name: support-agent
tools: [salesforce-service:*, sap-s4-orders:get_sales_order]
rename: {"salesforce-service:get_case": lookup_case}
allow_writes: true            # false makes the profile read-only
policies: [...]               # on top of each pack's policies
proxy: [...]                  # tools passed through from an upstream MCP server (docs/proxy.md)
tokenize_results: false       # true: identifiers in results are tokenised before the model sees them
timeout_s: 30
```

## Packs

A pack is everything for one system and use: `pack.yaml` (system, backends, settings, auth, fakes, validation
status), `tools.py` (typed tools), `backend.py` (how they reach the system), `policies.yaml`, `conformance.yaml` and a
README. Settings come from the environment (`${VAR:-default}`), so a pack is committed without environment-specific
values. A pack can also be generated from an OpenAPI spec (`kind: openapi`).

## The call pipeline

Every call, whether over MCP or the REST API, goes through the same steps:

1. **Inbound auth**: who is calling and for which end user (`HUB_AUTH`).
2. **Profile allowlist**: unknown tools are refused; a tool can be switched off at runtime.
3. **Arguments**: validated against the tool's schema (types, patterns, limits, no unknown fields).
4. **Writes**: refused in read-only profiles.
5. **Policies**: the pack's, then the profile's. Deny and rate limits stop the call; require-approval holds it.
6. **Identity**: the broker gets a credential for this user and this system (cached until near expiry).
7. **The tool**: business rules that need the system's data run here; a tool can also hold itself for approval
   (`ctx.require_approval`) when a threshold depends on data.
8. **Hooks and tokenisation**: `on_result` (e.g. DPDP), then tokenisation if the profile asks.
9. **Audit**: one row per call, with tokenised arguments.

Outcomes: `ok`, `error` (shown to the model), `denied`, `pending_approval`.
