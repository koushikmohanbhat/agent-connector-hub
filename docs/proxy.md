# Proxying vendor MCP servers

Packs give an agent a few business tools. When a vendor's MCP server already exposes the right tool, a profile can
pass it through directly, renamed and narrowed:

```yaml
proxy:
  - name: servicenow-requests
    url: "${SERVICENOW_MCP_URL}"
    system: servicenow
    auth: {identity: "${SERVICENOW_IDENTITY:-service_account}", token_url: "...", client_id_env: ..., client_secret_env: ...}
    tools:
      - upstream: get_records
        as: find_catalog_requests
        description: "Find service catalog requests with an encoded query."
        fixed_args: {table: sc_request}          # set by the hub, removed from the schema the model sees
        hide_args: [display_value]               # removed from the schema
        enum: {limit: [5, 10, 20]}               # narrowed choices
        writes: false                            # otherwise taken from the upstream readOnlyHint
```

Proxied tools get everything else the hub does: the profile's policies, identity, audit and the kill switch. Use them
for read tools with simple arguments. Anything with business rules or several upstream calls should be a pack tool.

The hub lists the upstream tools when the profile is first used; `hub upstream <pack>` lists a pack's upstream server.
