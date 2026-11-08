"""SAP S/4HANA through the AWS for SAP MCP Server's generic OData tools. The pack turns them into a few safe
business reads and one write, with fixed services and entity sets, so the model never writes free-form OData."""
from __future__ import annotations

from typing import Any

from connector_hub.sdk import ToolContext, ToolError, mcp_call

SO = ("API_SALES_ORDER_SRV", "A_SalesOrder")
SO_ITEM = ("API_SALES_ORDER_SRV", "A_SalesOrderItem")
DELIVERY_ITEM = ("API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem")
RETURN = ("API_CUSTOMER_RETURN_SRV", "A_CustomerReturn")


def lit(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


class AwsSapMcpBackend:
    def __init__(self, settings: dict[str, Any], pack_dir):
        self.s = settings
        self.a = settings.get("tool_args", {})

    def _args(self, entity: tuple[str, str], **kw) -> dict:
        out = {self.a.get("service", "service"): entity[0], self.a.get("entity_set", "entity_set"): entity[1]}
        for k, v in kw.items():
            if v is not None:
                out[self.a.get(k, k)] = v
        return out

    async def read(self, ctx: ToolContext, entity, *, filter: str | None = None, key: dict | None = None,
                   select: str | None = None, top: int | None = None) -> list[dict]:
        res = await mcp_call(ctx, self.s["mcp_url"], self.s["read_tool"],
                             self._args(entity, filter=filter, key=key, select=select, top=top))
        d = (res or {}).get("d", res or {})
        return d.get("results", [d] if d and "results" not in d else [])

    def sold_to_scope(self, ctx: ToolContext) -> str | None:
        """The sold-to party a customer user is limited to, or None for no extra filter.

        Sales ops see everything. When the hub acts as the user (principal propagation), SAP applies their own
        authorisations, and the claim, if present, is an extra check. With a technical user the claim is required:
        without it the user sees nothing (fail closed)."""
        if ctx.user.has("sales_ops"):
            return None
        claim = ctx.user.claims.get(self.s.get("customer_claim", "sap_customer"))
        if claim is None and not ctx.acts_as_user:
            raise ToolError("no SAP customer is linked to this user")
        return claim

    async def order(self, ctx, order_id: str) -> dict:
        try:
            rows = await self.read(ctx, SO, key={"SalesOrder": order_id})
        except ToolError:
            rows = []
        sold_to = self.sold_to_scope(ctx)
        if not rows or (sold_to is not None and rows[0].get("SoldToParty") != sold_to):
            raise ToolError(f"sales order {order_id} was not found")
        return rows[0]

    async def items(self, ctx, order_id: str) -> list[dict]:
        return await self.read(ctx, SO_ITEM, filter=f"SalesOrder eq {lit(order_id)}")

    async def deliveries(self, ctx, order_id: str) -> list[dict]:
        return await self.read(ctx, DELIVERY_ITEM, filter=f"ReferenceSDDocument eq {lit(order_id)}")

    async def open_orders(self, ctx, customer_id: str | None, limit: int) -> list[dict]:
        sold_to = self.sold_to_scope(ctx)
        if sold_to is not None:
            customer_id = sold_to
        terms = ["OverallSDProcessStatus ne 'C'"]
        if customer_id:
            terms.append(f"SoldToParty eq {lit(customer_id)}")
        return await self.read(ctx, SO, filter=" and ".join(terms), top=limit)

    async def create_return(self, ctx, payload: dict) -> dict:
        res = await mcp_call(ctx, self.s["mcp_url"], self.s["create_tool"], self._args(RETURN, payload=payload))
        return (res or {}).get("d", res or {})
