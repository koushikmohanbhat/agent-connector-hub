"""An emulator in the shape of the AWS for SAP MCP Server: generic OData tools (odata_read, odata_create,
odata_write) over S/4HANA services. The argument names here are the pack's defaults; check the deployed server's
listing (`hub upstream sap-s4-orders`) and adjust the pack's `tool_args` settings if they differ.

Data: API_SALES_ORDER_SRV (A_SalesOrder, A_SalesOrderItem), API_OUTBOUND_DELIVERY_SRV (A_OutbDeliveryItem),
API_CUSTOMER_RETURN_SRV (A_CustomerReturn). Access: users with `sales_ops` see all; customers see their sold-to
party only (fake users cust-001 -> 10100001, cust-002 -> 10100002), as SAP would via principal propagation."""
from __future__ import annotations

import re

from . import State, mcp_server

SEED = {
    "API_SALES_ORDER_SRV/A_SalesOrder": [
        {"SalesOrder": "4500001", "SoldToParty": "10100001", "SalesOrderDate": "2026-10-01", "TotalNetAmount": "84000.00",
         "TransactionCurrency": "INR", "OverallSDProcessStatus": "C", "OverallDeliveryStatus": "C",
         "PurchaseOrderByCustomer": "PO-7781"},
        {"SalesOrder": "4500002", "SoldToParty": "10100001", "SalesOrderDate": "2026-10-20", "TotalNetAmount": "12500.00",
         "TransactionCurrency": "INR", "OverallSDProcessStatus": "B", "OverallDeliveryStatus": "B",
         "PurchaseOrderByCustomer": "PO-7790"},
        {"SalesOrder": "4500003", "SoldToParty": "10100002", "SalesOrderDate": "2026-10-22", "TotalNetAmount": "230000.00",
         "TransactionCurrency": "INR", "OverallSDProcessStatus": "A", "OverallDeliveryStatus": "A",
         "PurchaseOrderByCustomer": "BL-118"},
    ],
    "API_SALES_ORDER_SRV/A_SalesOrderItem": [
        {"SalesOrder": "4500001", "SalesOrderItem": "10", "Material": "PUMP-200", "RequestedQuantity": "4",
         "NetAmount": "64000.00"},
        {"SalesOrder": "4500001", "SalesOrderItem": "20", "Material": "SEAL-KIT", "RequestedQuantity": "10",
         "NetAmount": "20000.00"},
        {"SalesOrder": "4500002", "SalesOrderItem": "10", "Material": "VALVE-50", "RequestedQuantity": "5",
         "NetAmount": "12500.00"},
        {"SalesOrder": "4500003", "SalesOrderItem": "10", "Material": "MOTOR-7K", "RequestedQuantity": "2",
         "NetAmount": "230000.00"},
    ],
    "API_OUTBOUND_DELIVERY_SRV/A_OutbDeliveryItem": [
        {"DeliveryDocument": "80000101", "DeliveryDocumentItem": "10", "ReferenceSDDocument": "4500001",
         "ReferenceSDDocumentItem": "10", "ActualDeliveryQuantity": "4", "GoodsMovementStatus": "C",
         "ActualGoodsMovementDate": "2026-10-05"},
        {"DeliveryDocument": "80000101", "DeliveryDocumentItem": "20", "ReferenceSDDocument": "4500001",
         "ReferenceSDDocumentItem": "20", "ActualDeliveryQuantity": "10", "GoodsMovementStatus": "C",
         "ActualGoodsMovementDate": "2026-10-05"},
        {"DeliveryDocument": "80000140", "DeliveryDocumentItem": "10", "ReferenceSDDocument": "4500002",
         "ReferenceSDDocumentItem": "10", "ActualDeliveryQuantity": "5", "GoodsMovementStatus": "A",
         "ActualGoodsMovementDate": None},
    ],
    "API_CUSTOMER_RETURN_SRV/A_CustomerReturn": [],
}
STATE = State(SEED)
CUSTOMERS = {"cust-001": "10100001", "cust-002": "10100002"}
_TERM = re.compile(r"^\s*(\w+)\s+(eq|ne)\s+'((?:[^']|'')*)'\s*$")


def _sold_to(claims: dict) -> str | None:
    if "sales_ops" in claims.get("roles", []):
        return None
    return CUSTOMERS.get(claims.get("sub"), "__none__")


def _visible(entity: str, rec: dict, sold_to: str | None) -> bool:
    if sold_to is None:
        return True
    orders = {o["SalesOrder"] for o in STATE.data["API_SALES_ORDER_SRV/A_SalesOrder"] if o["SoldToParty"] == sold_to}
    if entity.endswith("A_SalesOrder"):
        return rec["SoldToParty"] == sold_to
    if entity.endswith("A_SalesOrderItem"):
        return rec["SalesOrder"] in orders
    if entity.endswith("A_OutbDeliveryItem"):
        return rec["ReferenceSDDocument"] in orders
    if entity.endswith("A_CustomerReturn"):
        return rec.get("SoldToParty") == sold_to
    return False


def _filter(rec: dict, expr: str) -> bool:
    for term in [t for t in re.split(r"\s+and\s+", expr or "") if t.strip()]:
        m = _TERM.match(term)
        if not m:
            raise ValueError(f"unsupported $filter term {term!r} (the emulator supports eq / ne joined by and)")
        f, op, v = m.group(1), m.group(2), m.group(3).replace("''", "'")
        if (op == "eq") != (str(rec.get(f)) == v):
            return False
    return True


def _entity(a: dict) -> str:
    key = f"{a['service']}/{a['entity_set']}"
    if key not in STATE.data:
        raise ValueError(f"unknown entity set {key}")
    return key


def odata_read(claims: dict, a: dict) -> dict:
    ent = _entity(a)
    sold_to = _sold_to(claims)
    rows = [r for r in STATE.data[ent] if _visible(ent, r, sold_to) and _filter(r, a.get("filter", ""))]
    if a.get("key"):
        keys = a["key"]
        if not isinstance(keys, dict):
            raise ValueError("key must be an object of key fields, e.g. {\"SalesOrder\": \"4500001\"}")
        rows = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in keys.items())]
        if not rows:
            raise KeyError(f"{ent}: not found")
    top = int(a.get("top") or 100)
    select = [s for s in (a.get("select") or "").split(",") if s]
    rows = [{k: r.get(k) for k in select} if select else dict(r) for r in rows[: min(top, 100)]]
    return {"d": {"results": rows}}


def odata_create(claims: dict, a: dict) -> dict:
    ent = _entity(a)
    if not ent.endswith("A_CustomerReturn"):
        raise ValueError(f"creating {ent} is not supported by the emulator")
    payload = dict(a.get("payload") or {})
    order = next((o for o in STATE.data["API_SALES_ORDER_SRV/A_SalesOrder"]
                  if o["SalesOrder"] == payload.get("ReferenceSDDocument")), None)
    sold_to = _sold_to(claims)
    if order is None or (sold_to is not None and order["SoldToParty"] != sold_to):
        raise PermissionError("not authorised for this sales order")
    rec = {"CustomerReturn": f"6000{STATE.next():04d}", "SoldToParty": order["SoldToParty"],
           "CustomerReturnType": payload.get("CustomerReturnType", "CBRE"), "SDDocumentReason": payload.get("SDDocumentReason"),
           "ReferenceSDDocument": order["SalesOrder"], "to_Item": payload.get("to_Item", []), "OverallSDProcessStatus": "A"}
    STATE.data[ent].append(rec)
    return {"d": rec}


def odata_write(claims: dict, a: dict) -> dict:
    raise ValueError("updates are not supported by the emulator")



def mcp_app():
    s = {"type": "string"}
    base = {"service": s, "entity_set": s}
    return mcp_server("aws-for-sap-emulator", {
        "odata_read": ("Read entities from an SAP OData service.",
                       {"type": "object", "properties": {**base, "key": {}, "filter": s, "select": s,
                                                         "top": {"type": "integer"}}, "required": ["service", "entity_set"]},
                       odata_read, True),
        "odata_create": ("Create an entity in an SAP OData service.",
                         {"type": "object", "properties": {**base, "payload": {"type": "object"}},
                          "required": ["service", "entity_set", "payload"]}, odata_create, False),
        "odata_write": ("Update an entity in an SAP OData service.",
                        {"type": "object", "properties": {**base, "key": {}, "payload": {"type": "object"}},
                         "required": ["service", "entity_set", "key", "payload"]}, odata_write, False),
    }, STATE)
