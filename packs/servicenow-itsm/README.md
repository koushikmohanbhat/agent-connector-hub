# servicenow-itsm

Employee helpdesk tools: `get_incident`, `list_my_incidents`, `create_incident`, `add_work_note`,
`get_request_status`.

## Backends

| `SERVICENOW_BACKEND` | Reaches ServiceNow through | Notes |
|---|---|---|
| `mcp` (default) | the instance's MCP server (MCP Server Console) | Its tools depend on what the admin configured; map them with `SERVICENOW_MCP_READ_TOOL`, `..._CREATE_TOOL`, `..._UPDATE_TOOL` (generic table read / create / update) |
| `rest` | the Table API (`/api/now/table/...`) | `SERVICENOW_INSTANCE_URL` |

## Identity

ServiceNow recommends a service account for integrations (`SERVICENOW_IDENTITY=service_account`, OAuth client
credentials at `https://<instance>.service-now.com/oauth_token.do`). The pack then scopes every read and write to the
end user's own records (`caller_id` / `requested_for`), unless the user has the `itil` role, and the audit log records
who each call was for. Per-user OAuth (`token_exchange` or `user_oauth`) also works.

## Policies

A P1 (impact 1 and urgency 1) is held for `service_desk_lead`.

Validation: both backends fixture tested against the emulator; not yet live tested.
