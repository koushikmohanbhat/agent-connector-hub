# salesforce-service

Service Cloud tools for a support agent: `find_account`, `get_case`, `create_case`, `add_case_comment`,
`update_case_status`. The agent gets five business actions, never a SOQL console: queries are built inside the pack
with escaped values.

## Backends

| `SALESFORCE_BACKEND` | Reaches Salesforce through | Notes |
|---|---|---|
| `mcp` (default) | the org's Salesforce hosted MCP server | Map the upstream tool names with `SALESFORCE_MCP_QUERY_TOOL`, `..._CREATE_TOOL`, `..._UPDATE_TOOL`; list them with `hub upstream salesforce-service` |
| `rest` | the REST API (`/services/data/<version>/...`) | For orgs that have not enabled hosted MCP servers. `SALESFORCE_INSTANCE_URL`, `SALESFORCE_API_VERSION` |

Both backends give the same results (checked by `hub conformance`).

## Identity

Act as the user, so Salesforce applies their object, field and sharing permissions:
- `token_exchange`: your IdP exchanges the user's token for one Salesforce trusts (set `SALESFORCE_TOKEN_URL`,
  `SALESFORCE_AUDIENCE`, client credentials in `SALESFORCE_CLIENT_ID` / `SALESFORCE_CLIENT_SECRET`).
- `ema`: if your hosted MCP server uses MCP Enterprise-Managed Authorization (ID-JAG).
- `user_oauth`: each user connects their Salesforce account once (`/v1/links/salesforce/start`), using a connected
  app with refresh tokens enabled (`SALESFORCE_AUTHORIZE_URL`, `SALESFORCE_TOKEN_URL`).

## Policies

Closing a case is held for `support_lead`; public comments are capped at 2,000 characters; 60 calls a minute per user.

Validation: both backends fixture tested against the emulator; not yet live tested.
