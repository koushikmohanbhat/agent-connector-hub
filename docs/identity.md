# Identity

## Into the hub (`HUB_AUTH`)

| Mode | Use when | The end user is |
|---|---|---|
| `oidc` | the agent forwards the user's token from your IdP | the token's `sub` (roles and tenant from claims) |
| `api_key` | a trusted agent backend calls the hub (e.g. agent-starter-kit) | `X-End-User-Id` (+ `X-End-User-Roles`, `X-Tenant`) |
| `dev` | local only; refused unless `HUB_ENV=dev` | `X-User-Id` |

In `oidc` mode a 401 carries `WWW-Authenticate: Bearer resource_metadata=...`, and
`/.well-known/oauth-protected-resource/mcp/<profile>` names your IdP, as MCP clients expect.

## Out to each system (`auth.identity` in the pack)

| Mode | How | Notes |
|---|---|---|
| `token_exchange` | RFC 8693 at your IdP: the user's token (or a hub-signed assertion) for one with the system's audience and the tool's scopes | The default for systems that trust your IdP |
| `ema` | MCP Enterprise-Managed Authorization: token exchange for an ID-JAG, then a JWT-bearer grant at the system's authorization server | For MCP servers that declare the extension |
| `user_oauth` | the user links their account once (authorization code + PKCE); the hub keeps the refresh token encrypted (`HUB_LINK_KEY`) | For systems with their own OAuth and no IdP trust |
| `service_account` | client credentials or a static key | The pack must filter per user (`auth.user_filter`); the audit log records the user |
| `fake` | an unsigned token naming the user, for fakes | Refused when `HUB_ENV=prod` |

Tokens are cached per tenant, user, system and scopes until shortly before expiry, are never logged and never reach
the model.

**Hub-signed assertions.** With `HUB_AUTH=api_key` there is no user token to exchange, and an approved call runs
after the user's token may have expired. Set `HUB_ASSERTION_KEY` (or `HUB_ASSERTION_KEY_FILE`) to a key your IdP
trusts for JWT assertions; the hub then signs a five-minute assertion for the user (`sub`, `tenant`), with the
approver in `act` when it runs an approved call. Configure the IdP to accept it only from the hub's client.
