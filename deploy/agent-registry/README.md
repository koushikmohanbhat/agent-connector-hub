# With Gemini Enterprise Agent Platform: Agent Registry and Agent Gateway

On Google Cloud, Agent Registry is the catalog of approved MCP servers and tools, and Agent Gateway governs agent
egress to them (by default it blocks MCP servers that are not registered). Registering the hub makes its profiles
discoverable and lets security teams apply Agent Gateway policies on top of the hub's own.

## Steps

1. Deploy the hub on Cloud Run (`deploy/terraform/gcp-cloud-run`), with `HUB_AUTH=oidc` against your IdP.
2. Register each profile's endpoint (`https://<hub>/mcp/<profile>`) as a remote MCP server in Agent Registry, following
   Google's "Register MCP servers" guide, in the same region as your Agent Runtime agents.
3. Bind the agent's tool authentication to your IdP through the Agent Identity auth manager, so calls reach the hub
   with a token the hub accepts.
4. Route the agent through Agent Gateway; add IAM or MCP-attribute policies there if you need to restrict tools
   further.

The hub keeps doing what the Gateway doesn't: business-level tools, per-user downstream credentials, approvals,
tokenised audit and fakes for tests. Agent Gateway adds network-level governance and the registry.

Status: documented, not tested against a live Agent Registry.
