"""Calling upstream MCP servers (a vendor's hosted server, or a fake of one) with the end user's credential, and
exposing selected upstream tools through the hub, renamed and narrowed (profile `proxy:` entries).

Each call opens a short-lived client: MCP 2026-07-28 is stateless, so there is no session to keep. Older servers are
handled by the SDK's automatic version negotiation.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from . import fakes
from .config import ProxySpec
from .tools import HubTool, ToolContext, ToolError

FAKE_AUTH_META = "connector-hub/authorization"   # in-process fakes read the caller's credential from request _meta


def _target(url: str, token: str | None, header: str = "Authorization", scheme: str = "Bearer", timeout: float = 30.0):
    if fakes.is_fake(url):
        return fakes.resolve(url)[0]
    headers = {header: f"{scheme} {token}".strip()} if token else {}
    return streamable_http_client(url, http_client=httpx2.AsyncClient(headers=headers, timeout=timeout))


def result_value(r: Any) -> Any:
    if getattr(r, "structured_content", None) is not None:
        return r.structured_content
    texts = [c.text for c in (r.content or []) if getattr(c, "type", "") == "text"]
    text = "\n".join(texts)
    try:
        return json.loads(text)
    except ValueError:
        return text


async def call(url: str, tool: str, args: dict[str, Any], token: str | None, *, header: str = "Authorization",
               scheme: str = "Bearer", timeout: float = 30.0) -> Any:
    meta = {FAKE_AUTH_META: f"{scheme} {token}".strip()} if fakes.is_fake(url) and token else None
    async with Client(_target(url, token, header, scheme, timeout), read_timeout_seconds=timeout) as c:
        r = await c.call_tool(tool, args, meta=meta)
    value = result_value(r)
    if r.is_error:
        raise ToolError(f"{tool} failed upstream: {str(value)[:300]}")
    return value


async def list_tools(url: str, token: str | None, *, header: str = "Authorization", scheme: str = "Bearer") -> list[dict]:
    async with Client(_target(url, token, header, scheme)) as c:
        res = await c.list_tools()
    return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema,
             "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else {}} for t in res.tools]


def narrow(schema: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Remove fixed and hidden arguments from the schema the model sees, and add enums."""
    s = deepcopy(schema or {"type": "object", "properties": {}})
    props = s.setdefault("properties", {})
    gone = set(entry.get("fixed_args", {})) | set(entry.get("hide_args", []))
    for k in gone:
        props.pop(k, None)
    if "required" in s:
        s["required"] = [r for r in s["required"] if r not in gone]
    for k, values in (entry.get("enum") or {}).items():
        if k in props:
            props[k]["enum"] = list(values)
    s["additionalProperties"] = False
    return s


def proxy_tools(spec: ProxySpec, upstream: dict[str, dict]) -> list[HubTool]:
    """HubTools for a profile's proxy entry, given the upstream tool listing (name -> tool)."""
    out = []
    for entry in spec.tools:
        name = entry["upstream"]
        up = upstream.get(name)
        if up is None:
            raise ToolError(f"proxy {spec.name}: upstream server has no tool {name!r} "
                            f"(it lists: {', '.join(sorted(upstream)) or 'nothing'})")
        fixed = dict(entry.get("fixed_args", {}))

        async def handler(ctx: ToolContext, args: dict[str, Any], _name=name, _fixed=fixed) -> Any:
            return await call(spec.url, _name, {**args, **_fixed}, await ctx.token(), header=spec.auth.header,
                              scheme=spec.auth.scheme)

        writes = entry.get("writes")
        if writes is None:
            ann = up.get("annotations", {})
            writes = not ann.get("readOnlyHint", ann.get("read_only_hint", False))
        out.append(HubTool(entry.get("as", name), entry.get("description") or up["description"],
                           narrow(up["input_schema"], entry), handler, pack=f"proxy:{spec.name}", system=spec.system,
                           writes=bool(writes)))
    return out
