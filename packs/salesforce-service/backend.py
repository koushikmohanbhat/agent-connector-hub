"""Two ways to reach Salesforce with the same business logic: through the org's hosted MCP server, or the REST API.
Both run as the end user, so Salesforce's sharing rules decide what they see."""
from __future__ import annotations

import re
from typing import Any

from connector_hub.sdk import ToolContext, ToolError, check_response, http, mcp_call

CASE_FIELDS = "Id, CaseNumber, AccountId, Subject, Description, Status, Priority, Origin"


def q(value: str) -> str:
    """Quote a value for SOQL."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def like(value: str) -> str:
    return q("%" + re.sub(r"[%_]", "", value) + "%")


class Base:
    def __init__(self, settings: dict[str, Any], pack_dir):
        self.s = settings

    async def query(self, ctx: ToolContext, soql: str) -> list[dict]:
        raise NotImplementedError

    async def create(self, ctx: ToolContext, sobject: str, fields: dict) -> str:
        raise NotImplementedError

    async def update(self, ctx: ToolContext, sobject: str, rid: str, fields: dict) -> None:
        raise NotImplementedError

    # business operations ------------------------------------------------------------------------------------------
    async def find_accounts(self, ctx, name: str) -> list[dict]:
        rows = await self.query(ctx, f"SELECT Id, Name, Industry FROM Account WHERE Name LIKE {like(name)} "
                                     "ORDER BY Name LIMIT 10")
        return [{"account_id": r["Id"], "name": r["Name"], "industry": r.get("Industry")} for r in rows]

    async def case(self, ctx, case_number: str) -> dict:
        rows = await self.query(ctx, f"SELECT {CASE_FIELDS} FROM Case WHERE CaseNumber = {q(case_number)} LIMIT 1")
        if not rows:
            raise ToolError(f"case {case_number} was not found")
        return rows[0]

    async def comments(self, ctx, case_id: str) -> list[dict]:
        rows = await self.query(ctx, f"SELECT Id, CommentBody, IsPublished FROM CaseComment WHERE ParentId = {q(case_id)}")
        return [{"comment": r["CommentBody"], "public": bool(r.get("IsPublished"))} for r in rows]

    async def create_case(self, ctx, account_id: str, subject: str, description: str, priority: str) -> dict:
        accounts = await self.query(ctx, f"SELECT Id FROM Account WHERE Id = {q(account_id)} LIMIT 1")
        if not accounts:
            raise ToolError(f"account {account_id} was not found")
        rid = await self.create(ctx, "Case", {"AccountId": account_id, "Subject": subject, "Description": description,
                                              "Priority": priority, "Origin": "Agent"})
        rows = await self.query(ctx, f"SELECT {CASE_FIELDS} FROM Case WHERE Id = {q(rid)} LIMIT 1")
        return rows[0]

    async def add_comment(self, ctx, case_id: str, body: str, public: bool) -> None:
        await self.create(ctx, "CaseComment", {"ParentId": case_id, "CommentBody": body, "IsPublished": public})

    async def set_status(self, ctx, case_id: str, status: str) -> None:
        await self.update(ctx, "Case", case_id, {"Status": status})


class McpBackend(Base):
    """Through the org's hosted MCP server. Tool names are settings because they depend on the org's server."""

    async def query(self, ctx, soql):
        res = await mcp_call(ctx, self.s["mcp_url"], self.s["mcp_query_tool"], {"query": soql})
        return [{k: v for k, v in r.items() if k != "attributes"} for r in (res or {}).get("records", [])]

    async def create(self, ctx, sobject, fields):
        res = await mcp_call(ctx, self.s["mcp_url"], self.s["mcp_create_tool"], {"sobject": sobject, "fields": fields})
        if not res.get("success", True) or not res.get("id"):
            raise ToolError(f"Salesforce did not create the {sobject}")
        return res["id"]

    async def update(self, ctx, sobject, rid, fields):
        await mcp_call(ctx, self.s["mcp_url"], self.s["mcp_update_tool"],
                       {"sobject": sobject, "record_id": rid, "fields": fields})


class RestBackend(Base):
    """The REST API (/services/data/<version>/...), for orgs without the hosted MCP server enabled."""

    def _path(self, rest: str) -> str:
        return f"/services/data/{self.s['api_version']}/{rest}"

    async def query(self, ctx, soql):
        async with http(ctx, self.s["instance_url"]) as c:
            r = await c.get(self._path("query"), params={"q": soql})
        return [{k: v for k, v in rec.items() if k != "attributes"}
                for rec in check_response(r, "Salesforce").get("records", [])]

    async def create(self, ctx, sobject, fields):
        async with http(ctx, self.s["instance_url"]) as c:
            r = await c.post(self._path(f"sobjects/{sobject}"), json=fields)
        return check_response(r, "Salesforce")["id"]

    async def update(self, ctx, sobject, rid, fields):
        async with http(ctx, self.s["instance_url"]) as c:
            r = await c.patch(self._path(f"sobjects/{sobject}/{rid}"), json=fields)
        check_response(r, "Salesforce", f"{sobject} {rid} was not found")
