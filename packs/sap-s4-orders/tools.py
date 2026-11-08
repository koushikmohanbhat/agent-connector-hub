"""SAP S/4HANA order tools for a B2B customer-service agent."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from connector_hub.sdk import ToolContext, ToolError, tool

OrderId = Annotated[str, Field(pattern=r"^\d{1,10}$", description="SAP sales order number, e.g. 4500001")]
STATUS = {"A": "not started", "B": "partly done", "C": "complete", "": "not relevant"}


class ReturnItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: Annotated[str, Field(pattern=r"^\d{1,6}$", description="Sales order item, e.g. 10")]
    quantity: Annotated[float, Field(gt=0)]


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


@tool
async def get_sales_order(ctx: ToolContext, order_id: OrderId) -> dict:
    """Get a sales order with its items, amounts and processing and delivery status."""
    o = await ctx.backend.order(ctx, order_id)
    items = await ctx.backend.items(ctx, order_id)
    return {"order_id": o["SalesOrder"], "customer_po": o.get("PurchaseOrderByCustomer"), "date": o.get("SalesOrderDate"),
            "net_amount": _num(o.get("TotalNetAmount")), "currency": o.get("TransactionCurrency"),
            "status": STATUS.get(o.get("OverallSDProcessStatus") or "", o.get("OverallSDProcessStatus")),
            "delivery_status": STATUS.get(o.get("OverallDeliveryStatus") or "", o.get("OverallDeliveryStatus")),
            "items": [{"item": i["SalesOrderItem"], "material": i.get("Material"), "quantity": _num(i.get("RequestedQuantity")),
                       "net_amount": _num(i.get("NetAmount"))} for i in items]}


@tool
async def list_open_orders(ctx: ToolContext, customer_id: Annotated[str, Field(pattern=r"^\d{1,10}$")] | None = None,
                           limit: Annotated[int, Field(ge=1, le=50)] = 20) -> dict:
    """List open sales orders. Customers always see their own; sales ops can pass a customer (sold-to party) id."""
    rows = await ctx.backend.open_orders(ctx, customer_id, limit)
    return {"orders": [{"order_id": r["SalesOrder"], "date": r.get("SalesOrderDate"), "net_amount": _num(r.get("TotalNetAmount")),
                        "currency": r.get("TransactionCurrency"),
                        "delivery_status": STATUS.get(r.get("OverallDeliveryStatus") or "", r.get("OverallDeliveryStatus"))}
                       for r in rows]}


@tool
async def get_delivery_status(ctx: ToolContext, order_id: OrderId) -> dict:
    """Get the outbound deliveries for a sales order: delivery numbers, quantities and goods-issue status and date."""
    await ctx.backend.order(ctx, order_id)
    rows = await ctx.backend.deliveries(ctx, order_id)
    return {"order_id": order_id, "deliveries": [
        {"delivery": r["DeliveryDocument"], "order_item": r.get("ReferenceSDDocumentItem"),
         "quantity": _num(r.get("ActualDeliveryQuantity")),
         "goods_issued": r.get("GoodsMovementStatus") == "C", "goods_issue_date": r.get("ActualGoodsMovementDate")}
        for r in rows]}


@tool(writes=True)
async def create_return_request(ctx: ToolContext, order_id: OrderId,
                                items: Annotated[list[ReturnItem], Field(min_length=1, max_length=20)],
                                reason: Literal["damaged", "wrong_item", "not_needed"]) -> dict:
    """Request a customer return for delivered items of a sales order. Large returns wait for sales ops approval."""
    order = await ctx.backend.order(ctx, order_id)
    lines = {i["SalesOrderItem"]: i for i in await ctx.backend.items(ctx, order_id)}
    delivered = {}
    for d in await ctx.backend.deliveries(ctx, order_id):
        if d.get("GoodsMovementStatus") == "C":
            item = d.get("ReferenceSDDocumentItem")
            delivered[item] = delivered.get(item, 0) + _num(d.get("ActualDeliveryQuantity"))
    value, payload_items = 0.0, []
    for it in items:
        it = ReturnItem(**it) if isinstance(it, dict) else it
        line = lines.get(it.item)
        if line is None:
            raise ToolError(f"order {order_id} has no item {it.item}")
        if it.quantity > delivered.get(it.item, 0):
            raise ToolError(f"item {it.item}: only {delivered.get(it.item, 0):g} delivered, cannot return {it.quantity:g}")
        unit = _num(line.get("NetAmount")) / max(_num(line.get("RequestedQuantity")), 1)
        value += unit * it.quantity
        payload_items.append({"ReferenceSDDocumentItem": it.item, "Material": line.get("Material"),
                              "RequestedQuantity": f"{it.quantity:g}"})
    limit = float(ctx.settings.get("return_approval_limit") or 0)
    if limit and value > limit:
        ctx.require_approval(f"Return value {value:,.2f} {order.get('TransactionCurrency')} is above "
                             f"{limit:,.0f}; sales ops must approve it.", approver_role="sales_ops")
    payload = {"CustomerReturnType": ctx.settings.get("return_type", "CBRE"), "ReferenceSDDocument": order_id,
               "SDDocumentReason": ctx.settings.get("reason_codes", {}).get(reason), "to_Item": payload_items}
    r = await ctx.backend.create_return(ctx, payload)
    return {"return_id": r.get("CustomerReturn"), "order_id": order_id, "value": round(value, 2),
            "currency": order.get("TransactionCurrency"), "status": "requested"}
