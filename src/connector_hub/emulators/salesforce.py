"""A Salesforce emulator: a SOQL subset, sObject create / update, and sharing (portal users see their own account's
records; users with the `support_agent` role see all). Served two ways, like the real org:
  - REST:  /services/data/<version>/query, /sobjects/<type>, /sobjects/<type>/<id>
  - MCP:   soql_query, create_record, update_record (a stand-in for an org's hosted MCP server; real tool names vary
           by org configuration, so the pack maps them in settings)
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import State, claims_from_request, mcp_server

SEED = {
    "Account": [
        {"Id": "001A00000000001", "Name": "Asha Traders", "Industry": "Retail", "Phone": "+91 98450 12345",
         "OwnerPortalUser": "cust-001"},
        {"Id": "001A00000000002", "Name": "Bharat Logistics", "Industry": "Transportation", "Phone": "+91 99000 54321",
         "OwnerPortalUser": "cust-002"},
    ],
    "Contact": [
        {"Id": "003A00000000001", "AccountId": "001A00000000001", "Name": "Asha Rao", "Email": "asha@example.com"},
        {"Id": "003A00000000002", "AccountId": "001A00000000002", "Name": "Vikram Shah", "Email": "vikram@example.com"},
    ],
    "Case": [
        {"Id": "500A00000000001", "CaseNumber": "00001001", "AccountId": "001A00000000001", "Subject": "Late delivery",
         "Description": "Order ORD-10003 is late", "Status": "Working", "Priority": "Medium", "Origin": "Web"},
        {"Id": "500A00000000002", "CaseNumber": "00001002", "AccountId": "001A00000000002", "Subject": "Damaged pallet",
         "Description": "Pallet arrived damaged", "Status": "New", "Priority": "High", "Origin": "Phone"},
    ],
    "CaseComment": [],
}
STATE = State(SEED)
PREFIX = {"Account": "001", "Contact": "003", "Case": "500", "CaseComment": "00a"}

_SOQL = re.compile(r"^\s*SELECT\s+(?P<fields>.+?)\s+FROM\s+(?P<obj>\w+)(?:\s+WHERE\s+(?P<where>.+?))?"
                   r"(?:\s+ORDER\s+BY\s+(?P<order>\w+)(?:\s+(?P<dir>ASC|DESC))?)?(?:\s+LIMIT\s+(?P<limit>\d+))?\s*$", re.I | re.S)
_COND = re.compile(r"^\s*(\w+)\s*(=|!=|LIKE|IN)\s*(.+?)\s*$", re.I | re.S)


def _lit(v: str) -> Any:
    v = v.strip()
    if v.startswith("(") and v.endswith(")"):
        return [_lit(x) for x in re.findall(r"'(?:[^'\\]|\\.)*'|[^,\s]+", v[1:-1])]
    if v.startswith("'") and v.endswith("'"):
        return re.sub(r"\\(.)", r"\1", v[1:-1])
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.lower() == "null":
        return None
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        raise ValueError(f"unsupported SOQL literal {v!r}") from None


def _split_and(where: str) -> list[str]:
    parts, buf, q = [], "", False
    tokens = re.split(r"(\s+AND\s+)", where, flags=re.I)
    for t in tokens:
        if re.fullmatch(r"\s+AND\s+", t, re.I) and not q:
            parts.append(buf)
            buf = ""
        else:
            buf += t
            q = buf.count("'") % 2 == 1
    parts.append(buf)
    return [p for p in parts if p.strip()]


def _match(rec: dict, cond: str) -> bool:
    m = _COND.match(cond)
    if not m:
        raise ValueError(f"unsupported SOQL condition {cond!r}")
    field, op, raw = m.group(1), m.group(2).upper(), m.group(3)
    val, have = _lit(raw), rec.get(field)
    if op == "=":
        return have == val
    if op == "!=":
        return have != val
    if op == "IN":
        return have in (val if isinstance(val, list) else [val])
    pattern = "^" + re.escape(str(val)).replace("%", ".*").replace("_", ".") + "$"
    return have is not None and re.match(pattern, str(have), re.I) is not None


def visible_accounts(claims: dict) -> set[str] | None:
    if "support_agent" in claims.get("roles", []):
        return None
    return {a["Id"] for a in STATE.data["Account"] if a.get("OwnerPortalUser") == claims.get("sub")}


def _visible(obj: str, rec: dict, accounts: set[str] | None) -> bool:
    if accounts is None:
        return True
    if obj == "Account":
        return rec["Id"] in accounts
    if obj in ("Contact", "Case"):
        return rec.get("AccountId") in accounts
    if obj == "CaseComment":
        case = next((c for c in STATE.data["Case"] if c["Id"] == rec.get("ParentId")), None)
        return case is not None and case.get("AccountId") in accounts
    return False


def query(claims: dict, soql: str) -> dict:
    m = _SOQL.match(soql)
    if not m:
        raise ValueError("unsupported SOQL (the emulator supports SELECT .. FROM .. WHERE .. AND .. ORDER BY .. LIMIT)")
    obj = m.group("obj")
    if obj not in STATE.data:
        raise ValueError(f"sObject type '{obj}' is not supported")
    fields = [f.strip() for f in m.group("fields").split(",")]
    accounts = visible_accounts(claims)
    conds = _split_and(m.group("where")) if m.group("where") else []
    rows = [r for r in STATE.data[obj] if _visible(obj, r, accounts) and all(_match(r, c) for c in conds)]
    if m.group("order"):
        rows.sort(key=lambda r: str(r.get(m.group("order")) or ""), reverse=(m.group("dir") or "").upper() == "DESC")
    if m.group("limit"):
        rows = rows[: int(m.group("limit"))]
    recs = [{"attributes": {"type": obj}, **{f: r.get(f) for f in fields}} for r in rows]
    return {"totalSize": len(recs), "done": True, "records": recs}


def create(claims: dict, obj: str, fields: dict) -> dict:
    if obj not in STATE.data:
        raise ValueError(f"sObject type '{obj}' is not supported")
    accounts = visible_accounts(claims)
    rec = dict(fields)
    if obj == "Case":
        if accounts is not None and rec.get("AccountId") not in accounts:
            raise PermissionError("INSUFFICIENT_ACCESS_OR_READONLY: you cannot create cases on this account")
        rec.setdefault("Status", "New")
        rec["CaseNumber"] = f"{1002 + STATE.next():08d}"
    if obj == "CaseComment":
        case = next((c for c in STATE.data["Case"] if c["Id"] == rec.get("ParentId")), None)
        if case is None or not _visible("Case", case, accounts):
            raise PermissionError("INSUFFICIENT_ACCESS_OR_READONLY: case not found")
    rec["Id"] = f"{PREFIX[obj]}A{STATE.next() + 100:011d}"
    STATE.data[obj].append(rec)
    return {"id": rec["Id"], "success": True, "errors": []}


def update(claims: dict, obj: str, rid: str, fields: dict) -> dict:
    rec = next((r for r in STATE.data.get(obj, []) if r["Id"] == rid), None)
    if rec is None or not _visible(obj, rec, visible_accounts(claims)):
        raise KeyError(f"NOT_FOUND: {obj} {rid}")
    rec.update({k: v for k, v in fields.items() if k not in ("Id", "CaseNumber")})
    return {"id": rid, "success": True, "errors": []}


def rest_app() -> FastAPI:
    app = FastAPI()
    app.state.reset = STATE.reset

    def guard(fn):
        try:
            return fn()
        except PermissionError as e:
            code = 401 if str(e) == "unauthorized" else 403
            return JSONResponse([{"errorCode": "INSUFFICIENT_ACCESS" if code == 403 else "INVALID_SESSION_ID",
                                  "message": str(e)}], code)
        except KeyError as e:
            return JSONResponse([{"errorCode": "NOT_FOUND", "message": str(e)}], 404)
        except ValueError as e:
            return JSONResponse([{"errorCode": "MALFORMED_QUERY", "message": str(e)}], 400)

    @app.get("/services/data/{version}/query")
    def q(version: str, q: str, request: Request):
        return guard(lambda: query(claims_from_request(request), q))

    @app.post("/services/data/{version}/sobjects/{obj}")
    async def c(version: str, obj: str, request: Request):
        body = await request.json()
        return guard(lambda: JSONResponse(create(claims_from_request(request), obj, body), 201))

    @app.patch("/services/data/{version}/sobjects/{obj}/{rid}")
    async def u(version: str, obj: str, rid: str, request: Request):
        body = await request.json()
        res = guard(lambda: update(claims_from_request(request), obj, rid, body))
        return Response(status_code=204) if isinstance(res, dict) else res
    return app


def mcp_app():
    obj = {"type": "object"}
    return mcp_server("salesforce-emulator", {
        "soql_query": ("Run a SOQL query as the signed-in user.",
                       {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                       lambda cl, a: query(cl, a["query"]), True),
        "create_record": ("Create an sObject record.",
                          {"type": "object", "properties": {"sobject": {"type": "string"}, "fields": obj},
                           "required": ["sobject", "fields"]},
                          lambda cl, a: create(cl, a["sobject"], a["fields"]), False),
        "update_record": ("Update an sObject record.",
                          {"type": "object", "properties": {"sobject": {"type": "string"}, "record_id": {"type": "string"},
                                                            "fields": obj}, "required": ["sobject", "record_id", "fields"]},
                          lambda cl, a: update(cl, a["sobject"], a["record_id"], a["fields"]), False),
    }, STATE)
