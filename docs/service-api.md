# Service API

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /mcp/<profile>` | any caller (HUB_AUTH) | MCP Streamable HTTP: `tools/list`, `tools/call` (2026-07-28 and 2025-era clients) |
| `POST /v1/call/<profile>/<tool>` | any caller | the same call without MCP: `{"arguments": {...}}` → status, result, approval id, audit id, trace id |
| `GET /v1/profiles`, `GET /v1/profiles/<p>/tools` | any caller | profiles and their tools |
| `GET /v1/approvals?status=pending` | approvers | held calls the caller may decide |
| `POST /v1/approvals/<id>` | the approver role, same tenant, not the requester | `{"approve": bool, "reason": str}`; decided once |
| `GET /v1/audit?user_id=&tool=&limit=` | `hub_auditor` | audit rows for the caller's tenant |
| `DELETE /v1/users/<id>` | `privacy_admin` | erase linked accounts and pending held calls |
| `POST /v1/admin/profiles/<p>/tools/<t>/disable` (or `enable`) | `hub_admin` | kill switch |
| `GET /v1/links/<system>/start`, `GET /v1/links/callback` | the user | account linking for `user_oauth` |
| `GET /.well-known/oauth-protected-resource[/mcp/<p>]` | public (oidc) | RFC 9728 metadata |
| `GET /healthz`, `GET /readyz` | public | liveness; readiness (store reachable; no fakes when `HUB_ENV=prod`) |
| `GET /`, `POST /v1/dev/reset` | dev only | dev page; reset fakes |

Role names can be changed with `HUB_AUDITOR_ROLE`, `HUB_PRIVACY_ROLE` and `HUB_ADMIN_ROLE`. Tool results over MCP
carry `structuredContent` and a JSON text block; errors and denials set `isError`.
