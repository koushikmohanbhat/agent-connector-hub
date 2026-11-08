"""The hub service: one MCP endpoint per profile (/mcp/<profile>, Streamable HTTP, MCP 2026-07-28 and 2025-era clients),
plus a small REST API for approvals, audit, erasure, account linking, administration and health.

Inbound auth runs before MCP: an unauthenticated request gets 401 with an RFC 9728 resource_metadata pointer.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from mcp import types
from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__, fakes
from .auth import AuthError, authenticate, mode
from .engine import ApprovalError, Hub, Outcome
from .tools import Caller


def _caller_from_scope(scope) -> Caller:
    caller = scope.get("hub.caller")
    if caller is None:  # pragma: no cover - the router always sets it
        raise PermissionError("unauthenticated")
    return caller


def build_mcp(hub: Hub) -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        profile = ctx.request.scope["hub.profile"]
        tools = []
        for name, ex in (await hub.toolset(profile)).items():
            if (profile, name) in hub.disabled:
                continue
            tools.append(types.Tool(
                name=name, title=ex.tool.title, description=ex.tool.description, input_schema=ex.tool.input_schema,
                annotations=types.ToolAnnotations(read_only_hint=not ex.tool.writes, destructive_hint=ex.tool.writes,
                                                  open_world_hint=True)))
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params) -> types.CallToolResult:
        profile = ctx.request.scope["hub.profile"]
        caller = _caller_from_scope(ctx.request.scope)
        out = await hub.call(profile, params.name, params.arguments or {}, caller)
        return to_mcp(out)

    return Server("connector-hub", version=__version__, on_list_tools=list_tools, on_call_tool=call_tool,
                  instructions="Governed enterprise tools. A result with status pending_approval means a person must "
                               "approve the action first; tell the user it is waiting for approval.")


def to_mcp(out: Outcome) -> types.CallToolResult:
    payload = out.payload()
    structured = payload if isinstance(payload, dict) else {"result": payload}
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload, default=str))],
                                structured_content=structured, is_error=out.status in ("error", "denied"))


class McpRouter:
    """Maps /mcp/<profile> to the MCP app after authenticating the caller."""

    def __init__(self, app, hub: Hub):
        self.app, self.hub = app, hub

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == "/mcp" or path.startswith("/mcp/")):
            parts = path.split("/")
            profile = parts[2] if len(parts) > 2 and parts[2] else os.environ.get("HUB_DEFAULT_PROFILE", "")
            if profile not in self.hub.profiles:
                return await _json(send, 404, {"error": f"unknown profile {profile!r}"})
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
            try:
                caller = authenticate(headers)
            except AuthError as e:
                extra = []
                if e.status == 401 and mode() == "oidc":
                    meta = f"{_base(headers, scope)}/.well-known/oauth-protected-resource/mcp/{profile}"
                    extra = [(b"www-authenticate", f'Bearer resource_metadata="{meta}"'.encode())]
                return await _json(send, e.status, {"error": e.message}, extra)
            scope = dict(scope, path="/mcp", raw_path=b"/mcp")
            scope["hub.profile"], scope["hub.caller"] = profile, caller
        await self.app(scope, receive, send)


async def _json(send, status: int, body: dict, extra_headers: list | None = None):
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()),
                            *(extra_headers or [])]})
    await send({"type": "http.response.body", "body": data})


def _base(headers: dict, scope) -> str:
    if os.environ.get("HUB_PUBLIC_URL"):
        return os.environ["HUB_PUBLIC_URL"].rstrip("/")
    host = headers.get("host", "localhost")
    proto = headers.get("x-forwarded-proto", scope.get("scheme", "http"))
    return f"{proto}://{host}"


def create_app(hub_path: str | Path = ".", hub: Hub | None = None) -> FastAPI:
    mode()  # fail fast on a missing or unsafe HUB_AUTH
    hub = hub or Hub.from_path(hub_path)
    mcp_server = build_mcp(hub)
    allowed = [h.strip() for h in os.environ.get("HUB_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed,
                                         allowed_origins=[o.strip() for o in os.environ.get("HUB_ALLOWED_ORIGINS", "").split(",")
                                                          if o.strip()]) if allowed else \
        TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp_app = mcp_server.streamable_http_app(stateless_http=True, json_response=True, transport_security=security,
                                             host="0.0.0.0")  # noqa: S104 - only selects the rebinding defaults
    links_pending: dict[str, dict] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(title="connector-hub", version=__version__, lifespan=lifespan)
    app.state.hub = hub

    def caller(request: Request) -> Caller:
        try:
            return authenticate(request.headers)
        except AuthError as e:
            raise HTTPException(e.status, e.message) from None

    def need_role(c: Caller, env: str, default: str) -> None:
        role = os.environ.get(env, default)
        if not c.user.has(role):
            raise HTTPException(403, f"needs the {role} role")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/readyz")
    def readyz():
        packs = {n: {"backend": p.spec.backend, "identity": p.spec.auth.identity,
                     "on_fakes": p.spec.auth.identity == "fake" or any(fakes.is_fake(str(v)) for v in p.spec.settings.values())
                     or fakes.is_fake(str(p.spec.openapi.get("base_url", "")))}
                 for n, p in hub.packs.items()}
        try:
            hub.store.audit("__readyz__", limit=1)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ready": False, "error": f"store: {type(e).__name__}"}, 503)
        if os.environ.get("HUB_ENV", "").lower() == "prod" and any(v["on_fakes"] for v in packs.values()):
            return JSONResponse({"ready": False, "error": "packs on fakes in HUB_ENV=prod", "packs": packs}, 503)
        return {"ready": True, "packs": packs, "profiles": sorted(hub.profiles)}

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp/{profile}")
    def protected_resource(request: Request, profile: str = ""):
        if mode() != "oidc":
            raise HTTPException(404, "the hub is not using OIDC")
        base = _base(dict(request.headers), request.scope)
        return {"resource": f"{base}/mcp/{profile}" if profile else base,
                "authorization_servers": [os.environ["HUB_OIDC_ISSUER"]], "bearer_methods_supported": ["header"],
                "resource_name": f"connector-hub {profile}".strip()}

    @app.get("/v1/profiles")
    async def profiles(request: Request):
        caller(request)
        return [{"name": n, "description": p.description, "tools": len(await hub.toolset(n))} for n, p in hub.profiles.items()]

    @app.get("/v1/profiles/{profile}/tools")
    async def profile_tools(profile: str, request: Request):
        caller(request)
        if profile not in hub.profiles:
            raise HTTPException(404, "unknown profile")
        return [{"name": n, "description": e.tool.description, "pack": e.tool.pack, "system": e.tool.system,
                 "writes": e.tool.writes, "disabled": (profile, n) in hub.disabled, "input_schema": e.tool.input_schema}
                for n, e in (await hub.toolset(profile)).items()]

    @app.post("/v1/call/{profile}/{tool}")
    async def rest_call(profile: str, tool: str, request: Request, body: dict[str, Any] = Body(default={})):
        c = caller(request)
        if profile not in hub.profiles:
            raise HTTPException(404, "unknown profile")
        out = await hub.call(profile, tool, body.get("arguments", {}), c)
        return {"status": out.status, "result": out.payload(), "approval_id": out.approval_id,
                "audit_id": out.audit_id, "trace_id": out.trace_id}

    @app.get("/v1/approvals")
    def list_approvals(request: Request, status: str = "pending"):
        c = caller(request)
        rows = [r for r in hub.store.list_approvals(c.user.tenant, status) if c.user.has(r["approver_role"])]
        return [{k: r[k] for k in ("id", "profile", "tool", "args", "user", "status", "approver_role", "message",
                                   "created", "approver", "reason", "result")} for r in rows]

    @app.post("/v1/approvals/{approval_id}")
    async def decide(approval_id: str, request: Request, body: dict[str, Any] = Body(...)):
        c = caller(request)
        if not isinstance(body.get("approve"), bool):
            raise HTTPException(422, "body needs approve: true or false")
        try:
            return await hub.decide(approval_id, body["approve"], c.user, str(body.get("reason", ""))[:2000])
        except ApprovalError as e:
            raise HTTPException(e.status, e.message) from None

    @app.get("/v1/audit")
    def audit(request: Request, user_id: str | None = None, tool: str | None = None, limit: int = 100):
        c = caller(request)
        need_role(c, "HUB_AUDITOR_ROLE", "hub_auditor")
        return hub.store.audit(c.user.tenant, user_id=user_id, tool=tool, limit=min(max(limit, 1), 1000))

    @app.delete("/v1/users/{user_id}")
    def erase(user_id: str, request: Request):
        c = caller(request)
        need_role(c, "HUB_PRIVACY_ROLE", "privacy_admin")
        return {"user_id": user_id, "deleted": hub.erase_user(c.user.tenant, user_id)}

    @app.post("/v1/admin/profiles/{profile}/tools/{tool}/{action}")
    def admin_tool(profile: str, tool: str, action: str, request: Request):
        c = caller(request)
        need_role(c, "HUB_ADMIN_ROLE", "hub_admin")
        if profile not in hub.profiles or action not in ("disable", "enable"):
            raise HTTPException(404, "unknown profile or action")
        (hub.disabled.add if action == "disable" else hub.disabled.discard)((profile, tool))
        return {"profile": profile, "tool": tool, "disabled": (profile, tool) in hub.disabled}

    def _system_auth(system: str):
        for p in hub.packs.values():
            if p.spec.system == system:
                return p.spec.auth
        raise HTTPException(404, f"no pack for system {system!r}")

    @app.get("/v1/links/{system}/start")
    def link_start(system: str, request: Request):
        c = caller(request)
        auth = _system_auth(system)
        if auth.identity != "user_oauth":
            raise HTTPException(400, f"{system} does not use account linking")
        redirect = f"{_base(dict(request.headers), request.scope)}/v1/links/callback"
        url, pending = hub.broker.start_link(auth, system, c.user.as_dict(), redirect)
        for k in [k for k, v in links_pending.items() if v["expires"] < time.time()]:
            links_pending.pop(k, None)
        links_pending[pending["state"]] = pending
        return {"authorize_url": url}

    @app.get("/v1/links/callback")
    async def link_callback(state: str, code: str):
        pending = links_pending.pop(state, None)
        if pending is None or pending["expires"] < time.time():
            raise HTTPException(400, "unknown or expired link request")
        await hub.broker.finish_link(_system_auth(pending["system"]), pending, code)
        return HTMLResponse(f"<p>Your {pending['system']} account is connected. You can close this window.</p>")

    if os.environ.get("HUB_ENV", "").lower() == "dev":
        from .devpage import PAGE

        @app.get("/", response_class=HTMLResponse)
        def dev_page():
            return PAGE

        @app.post("/v1/dev/reset")
        def dev_reset():
            n = 0
            for target in fakes.registered().values():
                reset = getattr(target, "reset", None) or getattr(getattr(target, "state", None), "reset", None)
                if callable(reset):
                    reset()
                    n += 1
            return {"reset": n}

    app.mount("/", McpRouter(mcp_app, hub))
    return app
