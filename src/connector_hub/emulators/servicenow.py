"""A ServiceNow emulator: the Table API (incident, sc_request) with encoded queries and journal work notes, and
caller-based access (users without the `itil` role see only records where they are the caller; service accounts,
named svc-*, see all). Served as REST (/api/now/table/<table>) and as an MCP stand-in (get_records, create_record,
update_record; a real instance's MCP server exposes whatever tools its admin configured, so the pack maps names)."""
from __future__ import annotations

import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import State, claims_from_request, mcp_server

SEED = {
    "incident": [
        {"sys_id": "a1", "number": "INC0010001", "short_description": "VPN drops every hour", "description": "",
         "state": "2", "urgency": "2", "impact": "2", "priority": "3", "caller_id": "emp-001",
         "assignment_group": "Network", "opened_at": "2026-11-02 09:12:00", "work_notes": []},
        {"sys_id": "a2", "number": "INC0010002", "short_description": "Laptop will not boot", "description": "",
         "state": "1", "urgency": "1", "impact": "2", "priority": "2", "caller_id": "emp-002",
         "assignment_group": "Desktop", "opened_at": "2026-11-05 14:40:00", "work_notes": []},
    ],
    "sc_request": [
        {"sys_id": "r1", "number": "REQ0020001", "short_description": "New monitor", "request_state": "in_process",
         "approval": "approved", "requested_for": "emp-001", "opened_at": "2026-10-28 11:00:00", "stage": "fulfillment"},
    ],
}
STATE = State(SEED)
PRIORITY = {("1", "1"): "1", ("1", "2"): "2", ("2", "1"): "2", ("2", "2"): "3", ("1", "3"): "3", ("3", "1"): "3",
            ("2", "3"): "4", ("3", "2"): "4", ("3", "3"): "5"}
OWNER = {"incident": "caller_id", "sc_request": "requested_for"}


def _sees_all(claims: dict) -> bool:
    return "itil" in claims.get("roles", []) or str(claims.get("sub", "")).startswith("svc-")


def _visible(table: str, rec: dict, claims: dict) -> bool:
    return _sees_all(claims) or rec.get(OWNER[table]) == claims.get("sub")


def _matches(rec: dict, query: str) -> bool:
    for part in [p for p in query.split("^") if p and not p.startswith("ORDERBY")]:
        m = re.match(r"^(\w+)(!=|=|LIKE)(.*)$", part)
        if not m:
            raise ValueError(f"unsupported encoded query term {part!r}")
        f, op, v = m.groups()
        have = str(rec.get(f, ""))
        if (op == "=" and have != v) or (op == "!=" and have == v) or (op == "LIKE" and v.lower() not in have.lower()):
            return False
    return True


def _public(rec: dict, fields: list[str] | None) -> dict:
    out = {k: v for k, v in rec.items() if k != "work_notes"}
    out["work_notes"] = "\n".join(f"{n['at']} - {n['by']}: {n['text']}" for n in rec.get("work_notes", [])) \
        if "work_notes" in rec else None
    if out["work_notes"] is None:
        out.pop("work_notes")
    return {k: out.get(k) for k in fields} if fields else out


def get_records(claims: dict, table: str, query: str = "", fields: list[str] | None = None, limit: int = 20) -> list[dict]:
    if table not in STATE.data:
        raise ValueError(f"table {table} is not supported")
    rows = [r for r in STATE.data[table] if _visible(table, r, claims) and _matches(r, query)]
    order = re.search(r"\^?ORDERBY(DESC)?(\w+)", query)
    if order:
        rows.sort(key=lambda r: str(r.get(order.group(2), "")), reverse=bool(order.group(1)))
    return [_public(r, fields) for r in rows[: max(1, min(limit, 100))]]


def create_record(claims: dict, table: str, values: dict) -> dict:
    if table != "incident":
        raise ValueError(f"creating {table} records is not supported")
    if not _sees_all(claims) and values.get("caller_id") not in (None, claims.get("sub")):
        raise PermissionError("you can only raise incidents for yourself")
    n = STATE.next()
    rec = {"sys_id": uuid.uuid4().hex[:12], "number": f"INC{10002 + n:07d}", "state": "1", "description": "",
           "assignment_group": "", "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"), "work_notes": [],
           "caller_id": claims.get("sub"), **{k: str(v) for k, v in values.items() if k != "work_notes"}}
    rec["priority"] = PRIORITY.get((rec.get("impact", "3"), rec.get("urgency", "3")), "4")
    STATE.data[table].append(rec)
    return _public(rec, None)


def update_record(claims: dict, table: str, sys_id: str, values: dict) -> dict:
    rec = next((r for r in STATE.data.get(table, []) if r["sys_id"] == sys_id), None)
    if rec is None or not _visible(table, rec, claims):
        raise KeyError(f"record not found: {table} {sys_id}")
    for k, v in values.items():
        if k == "work_notes":
            rec["work_notes"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "by": claims.get("sub"), "text": str(v)})
        elif k not in ("sys_id", "number"):
            rec[k] = str(v)
    return _public(rec, None)


def rest_app() -> FastAPI:
    app = FastAPI()
    app.state.reset = STATE.reset

    def guard(fn):
        try:
            return {"result": fn()}
        except PermissionError as e:
            return JSONResponse({"error": {"message": str(e)}, "status": "failure"}, 401 if str(e) == "unauthorized" else 403)
        except KeyError as e:
            return JSONResponse({"error": {"message": str(e)}, "status": "failure"}, 404)
        except ValueError as e:
            return JSONResponse({"error": {"message": str(e)}, "status": "failure"}, 400)

    @app.get("/api/now/table/{table}")
    def list_(table: str, request: Request, sysparm_query: str = "", sysparm_fields: str = "", sysparm_limit: int = 20):
        fields = [f for f in sysparm_fields.split(",") if f] or None
        return guard(lambda: get_records(claims_from_request(request), table, sysparm_query, fields, sysparm_limit))

    @app.post("/api/now/table/{table}")
    async def create(table: str, request: Request):
        body = await request.json()
        return guard(lambda: create_record(claims_from_request(request), table, body))

    @app.patch("/api/now/table/{table}/{sys_id}")
    async def patch(table: str, sys_id: str, request: Request):
        body = await request.json()
        return guard(lambda: update_record(claims_from_request(request), table, sys_id, body))
    return app


def mcp_app():
    s = {"type": "string"}
    return mcp_server("servicenow-emulator", {
        "get_records": ("Read records from a table with an encoded query.",
                        {"type": "object", "properties": {"table": s, "query": s, "fields": {"type": "array", "items": s},
                                                          "limit": {"type": "integer"}}, "required": ["table"]},
                        lambda cl, a: {"records": get_records(cl, a["table"], a.get("query", ""), a.get("fields"),
                                                              int(a.get("limit", 20)))}, True),
        "create_record": ("Create a record in a table.",
                          {"type": "object", "properties": {"table": s, "values": {"type": "object"}},
                           "required": ["table", "values"]},
                          lambda cl, a: {"record": create_record(cl, a["table"], a["values"])}, False),
        "update_record": ("Update a record in a table.",
                          {"type": "object", "properties": {"table": s, "sys_id": s, "values": {"type": "object"}},
                           "required": ["table", "sys_id", "values"]},
                          lambda cl, a: {"record": update_record(cl, a["table"], a["sys_id"], a["values"])}, False),
    }, STATE)

