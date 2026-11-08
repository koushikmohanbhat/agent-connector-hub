"""In-process emulators of enterprise systems, for fakes. Each emulates the slice of the real API the packs use,
including per-user access, so a pack's real request path can run offline. They are test doubles, not the products."""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

from mcp import types
from mcp.server import Server

from ..fakes import read_fake_token

FAKE_AUTH_META = "connector-hub/authorization"


class State:
    """Mutable emulator data with a reset to the seed."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = copy.deepcopy(seed)
        self.data = copy.deepcopy(seed)
        self.counter = 0

    def reset(self) -> None:
        self.data = copy.deepcopy(self._seed)
        self.counter = 0

    def next(self) -> int:
        self.counter += 1
        return self.counter


ToolFn = Callable[[dict[str, Any], dict[str, Any]], Any]


def mcp_server(name: str, tools: dict[str, tuple[str, dict, ToolFn, bool]], state: State) -> Server:
    """tools: name -> (description, input schema, fn(claims, args), read_only)."""

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[
            types.Tool(name=n, description=d, input_schema=s,
                       annotations=types.ToolAnnotations(read_only_hint=ro, destructive_hint=not ro))
            for n, (d, s, _f, ro) in tools.items()])

    async def call_tool(ctx, params):
        meta = dict(ctx.meta or {})
        claims = read_fake_token(meta.get(FAKE_AUTH_META))
        if claims is None:
            return _err("unauthorized: no valid credential")
        if params.name not in tools:
            return _err(f"unknown tool {params.name}")
        try:
            value = tools[params.name][2](claims, dict(params.arguments or {}))
        except (KeyError, ValueError, PermissionError) as e:
            return _err(str(e))
        structured = value if isinstance(value, dict) else {"result": value}
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(value, default=str))],
                                    structured_content=structured)

    server = Server(name, on_list_tools=list_tools, on_call_tool=call_tool)
    server.reset = state.reset          # used by /v1/dev/reset and tests
    server.state = state
    return server


def _err(msg: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=msg)], is_error=True)


def claims_from_request(request) -> dict[str, Any]:
    c = read_fake_token(request.headers.get("authorization"))
    if c is None:
        raise PermissionError("unauthorized")
    return c
