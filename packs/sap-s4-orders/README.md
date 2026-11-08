# sap-s4-orders

B2B customer-service tools over SAP S/4HANA: `get_sales_order`, `list_open_orders`, `get_delivery_status`,
`create_return_request`.

## How it reaches SAP

Through the AWS for SAP MCP Server (GA on Amazon Bedrock AgentCore since May 2026), which exposes generic OData tools.
The pack fixes the services and entity sets (API_SALES_ORDER_SRV, API_OUTBOUND_DELIVERY_SRV,
API_CUSTOMER_RETURN_SRV), so the model never writes free-form OData.

- `SAP_MCP_URL`: the server's URL on AgentCore.
- Tool and argument names default to `odata_read` / `odata_create` with `service`, `entity_set`, `key`, `filter`,
  `select`, `top`, `payload`. Check them with `hub upstream sap-s4-orders` and adjust `settings.tool_args` in
  `pack.yaml` if your deployment differs.
- The return payload (`CustomerReturnType`, `SDDocumentReason`, `to_Item`) follows API_CUSTOMER_RETURN_SRV; check
  the fields your system requires (sales area, partner functions) and extend the tool.

AWS only. On Google Cloud or on-prem this pack runs only on its fake; a direct OData backend is on the roadmap.

## Identity

With principal propagation (`token_exchange`), SAP applies the user's own authorisations. With a technical user
(`service_account`), the pack requires the user's sold-to party in the `sap_customer` claim (`SAP_CUSTOMER_CLAIM`)
and filters by it; without the claim, a customer sees nothing. Users with `sales_ops` see all customers.

## Rules

Only delivered quantities can be returned. A return worth more than `SAP_RETURN_APPROVAL_LIMIT` (default 50,000) is
held by the tool itself for `sales_ops`, because the value depends on order data.

Validation: fixture tested against an emulator of the server's tool shape; not yet live tested.
