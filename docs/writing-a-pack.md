# Writing a pack

`hub new pack fleet-api` creates a working skeleton (tools, backend, fake, policies, conformance cases) that passes
`hub conformance fleet-api`. `packs/insurance-core` is the full example.

## Tools

```python
from typing import Annotated
from pydantic import Field
from connector_hub.sdk import ToolContext, ToolError, tool

@tool(writes=True)
async def open_claim(ctx: ToolContext, policy_number: Annotated[str, Field(pattern=r"^POL-\d{4,8}$")],
                     estimated_amount: Annotated[float, Field(gt=0)]) -> dict:
    """Register a first notice of loss on an active policy."""
    p = await ctx.backend.policy(ctx, policy_number)
    if p["status"] != "active":
        raise ToolError(f"policy {policy_number} is {p['status']}")
    return await ctx.backend.open_claim(ctx, {...})
```

- The schema comes from the type hints; use `Annotated` + `Field` for patterns and limits. The docstring is what the
  model reads: say what the tool returns and when to use it.
- Mark tools that change data with `writes=True` (conformance checks names like `create_`, `update_`, `open_`).
- Raise `ToolError` with a message the model can act on. Other exceptions become "failed; reference <trace id>".
- `ctx.user` is the verified end user; `ctx.settings` the pack settings; `ctx.acts_as_user` says whether the system
  sees the user's own identity.

## Backends

A backend class takes `(settings, pack_dir)` and offers business methods. Reach the system with
`connector_hub.sdk.http(ctx, base_url)` (an HTTP client with the user's credential) or
`connector_hub.sdk.mcp_call(ctx, url, tool, args)` (an upstream MCP server). Several backends can share one base
class, as `salesforce-service` does for `mcp` and `rest`; conformance then checks they agree.

## Fakes

`fakes:` in `pack.yaml` maps a host to an ASGI app or MCP server: `insurance-core: mock_api.app:create_app`. Point
settings at `fake://insurance-core` by default. A fake should apply per-user access the way the system does; read
the user from the fake token with `connector_hub.fakes.read_fake_token`. Give it `app.state.reset` (or `reset` on an
MCP server) so tests and `/v1/dev/reset` can restore the seed.

## Conformance cases

`conformance.yaml` lists calls and expected outcomes, including other users' records being hidden:

```yaml
calls:
  - {tool: get_policy, user: {id: cust-001}, args: {policy_number: POL-1001}, expect: {status: ok, equals: {status: active}}}
  - {tool: get_policy, user: {id: cust-001}, args: {policy_number: POL-2001}, expect: {status: error}}
```

Partner packs can live in their own repos: point `packs_dir` at them, or copy them into `packs/`.
