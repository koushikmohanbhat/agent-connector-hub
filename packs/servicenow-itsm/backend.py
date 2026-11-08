"""ServiceNow through the instance's MCP server or the Table API. Reads and writes are always scoped to the end user
(their own incidents and requests) unless the user has the itil role, so a service account cannot leak other
employees' tickets."""
from __future__ import annotations

from typing import Any

from connector_hub.sdk import ToolContext, ToolError, check_response, http, mcp_call

INCIDENT_FIELDS = ["sys_id", "number", "short_description", "description", "state", "urgency", "impact", "priority",
                   "caller_id", "assignment_group", "opened_at", "work_notes"]
REQUEST_FIELDS = ["sys_id", "number", "short_description", "request_state", "approval", "stage", "requested_for",
                  "opened_at"]


def enc(v: str) -> str:
    """Values in encoded queries cannot contain the ^ separator."""
    if "^" in str(v):
        raise ToolError("values cannot contain ^")
    return str(v)


class Base:
    def __init__(self, settings: dict[str, Any], pack_dir):
        self.s = settings

    async def read(self, ctx, table: str, query: str, fields: list[str], limit: int) -> list[dict]:
        raise NotImplementedError

    async def create(self, ctx, table: str, values: dict) -> dict:
        raise NotImplementedError

    async def update(self, ctx, table: str, sys_id: str, values: dict) -> dict:
        raise NotImplementedError

    def _scope(self, ctx: ToolContext, table: str) -> str:
        if ctx.user.has("itil"):
            return ""
        field = self.s.get("user_field", "caller_id") if table == "incident" else "requested_for"
        return f"{field}={enc(ctx.user.id)}^"

    async def one(self, ctx, table: str, number: str, fields: list[str]) -> dict:
        rows = await self.read(ctx, table, f"{self._scope(ctx, table)}number={enc(number)}", fields, 1)
        if not rows:
            raise ToolError(f"{number} was not found")
        return rows[0]

    async def my_incidents(self, ctx, open_only: bool, limit: int) -> list[dict]:
        q = self._scope(ctx, "incident") + ("state!=7^state!=6^" if open_only else "") + "ORDERBYDESCopened_at"
        return await self.read(ctx, "incident", q, INCIDENT_FIELDS[:9], limit)

    async def new_incident(self, ctx, values: dict) -> dict:
        if not ctx.user.has("itil"):
            values = {**values, self.s.get("user_field", "caller_id"): ctx.user.id}
        return await self.create(ctx, "incident", values)

    async def note(self, ctx, number: str, text: str) -> dict:
        rec = await self.one(ctx, "incident", number, ["sys_id", "number", "state"])
        return await self.update(ctx, "incident", rec["sys_id"], {"work_notes": text})


class McpBackend(Base):
    async def read(self, ctx, table, query, fields, limit):
        res = await mcp_call(ctx, self.s["mcp_url"], self.s["mcp_read_tool"],
                             {"table": table, "query": query, "fields": fields, "limit": limit})
        return (res or {}).get("records", [])

    async def create(self, ctx, table, values):
        res = await mcp_call(ctx, self.s["mcp_url"], self.s["mcp_create_tool"], {"table": table, "values": values})
        return (res or {}).get("record", {})

    async def update(self, ctx, table, sys_id, values):
        res = await mcp_call(ctx, self.s["mcp_url"], self.s["mcp_update_tool"],
                             {"table": table, "sys_id": sys_id, "values": values})
        return (res or {}).get("record", {})


class RestBackend(Base):
    async def read(self, ctx, table, query, fields, limit):
        async with http(ctx, self.s["instance_url"], extra_headers={"Accept": "application/json"}) as c:
            r = await c.get(f"/api/now/table/{table}", params={"sysparm_query": query, "sysparm_fields": ",".join(fields),
                                                               "sysparm_limit": limit, "sysparm_display_value": "false"})
        return check_response(r, "ServiceNow").get("result", [])

    async def create(self, ctx, table, values):
        async with http(ctx, self.s["instance_url"], extra_headers={"Accept": "application/json"}) as c:
            r = await c.post(f"/api/now/table/{table}", json=values)
        return check_response(r, "ServiceNow").get("result", {})

    async def update(self, ctx, table, sys_id, values):
        async with http(ctx, self.s["instance_url"], extra_headers={"Accept": "application/json"}) as c:
            r = await c.patch(f"/api/now/table/{table}/{sys_id}", json=values)
        return check_response(r, "ServiceNow", f"{table} record was not found").get("result", {})
