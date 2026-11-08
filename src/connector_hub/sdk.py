"""What a connector pack imports. Keep packs on this module so they survive internal changes.

    from connector_hub.sdk import tool, ToolContext, ToolError, http, mcp_call
"""
from __future__ import annotations

import contextlib
from typing import Any

from . import fakes, upstream
from .tools import ApprovalNeeded, ToolContext, ToolError, User, tool

__all__ = ["ApprovalNeeded", "ToolContext", "ToolError", "User", "tool", "http", "mcp_call", "check_response"]


@contextlib.asynccontextmanager
async def http(ctx: ToolContext, base_url: str, *, timeout: float = 30.0, scheme: str = "Bearer",
               header: str = "Authorization", extra_headers: dict[str, str] | None = None):
    """An HTTP client for the system, carrying a credential for the current end user. fake:// URLs go to fakes."""
    if not base_url:
        raise ToolError(f"{ctx.pack}: the system's base URL is not configured")
    token = await ctx.token()
    headers = dict(extra_headers or {})
    if token:
        headers[header] = f"{scheme} {token}".strip()
    async with fakes.http_client(base_url, headers=headers, timeout=timeout) as c:
        yield c


async def mcp_call(ctx: ToolContext, url: str, tool_name: str, args: dict[str, Any]) -> Any:
    """Call a tool on an upstream MCP server (e.g. the vendor's) as the current end user."""
    if not url:
        raise ToolError(f"{ctx.pack}: the upstream MCP server URL is not configured")
    return await upstream.call(url, tool_name, args, await ctx.token())


def check_response(r, system: str, not_found: str = "not found") -> Any:
    """Turn an HTTP response into data, or into a ToolError the model can read (no bodies, no stack traces)."""
    if r.status_code == 404:
        raise ToolError(not_found)
    if r.status_code in (401, 403):
        raise ToolError(f"{system} refused the request for this user ({r.status_code})")
    if r.status_code >= 400:
        raise ToolError(f"{system} returned an error ({r.status_code})")
    if r.status_code == 204 or not r.content:
        return {}
    return r.json()
