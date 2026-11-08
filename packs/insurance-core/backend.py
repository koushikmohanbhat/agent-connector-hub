"""The core insurance platform's REST API. Replace the paths with your platform's; keep the methods."""
from __future__ import annotations

from typing import Any

from connector_hub.sdk import ToolContext, check_response, http


class RestBackend:
    def __init__(self, settings: dict[str, Any], pack_dir):
        self.s = settings

    async def get(self, ctx: ToolContext, path: str, not_found: str) -> dict:
        async with http(ctx, self.s["base_url"]) as c:
            return check_response(await c.get(path), "the policy system", not_found)

    async def post(self, ctx: ToolContext, path: str, body: dict, not_found: str) -> dict:
        async with http(ctx, self.s["base_url"]) as c:
            return check_response(await c.post(path, json=body), "the policy system", not_found)

    async def policy(self, ctx, number: str) -> dict:
        return await self.get(ctx, f"/v1/policies/{number}", f"policy {number} was not found")

    async def claims(self, ctx, number: str) -> list[dict]:
        return (await self.get(ctx, f"/v1/policies/{number}/claims", f"policy {number} was not found"))["claims"]

    async def claim(self, ctx, claim_id: str) -> dict:
        return await self.get(ctx, f"/v1/claims/{claim_id}", f"claim {claim_id} was not found")

    async def open_claim(self, ctx, body: dict) -> dict:
        return await self.post(ctx, "/v1/claims", body, "policy was not found")

    async def add_document(self, ctx, claim_id: str, body: dict) -> dict:
        return await self.post(ctx, f"/v1/claims/{claim_id}/documents", body, f"claim {claim_id} was not found")
