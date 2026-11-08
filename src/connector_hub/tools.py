"""The tool contract. A pack tool is a typed function whose first parameter is the hidden `ctx: ToolContext`:

    @tool(writes=True)
    async def open_claim(ctx: ToolContext, policy_number: str, estimated_amount: float, description: str) -> dict:
        '''Open a claim on an active policy.'''
        return await ctx.backend.open_claim(ctx, policy_number, estimated_amount, description)

The JSON schema comes from the type hints (pydantic), the description from the docstring. `ctx` carries the verified
end user, the pack's backend and settings, and `await ctx.token()` for a credential to the system on that user's behalf.
The model never sees `ctx`.
"""
from __future__ import annotations

import inspect
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, create_model


class ToolError(Exception):
    """A failure the model should see (bad input, not found, not allowed). Message is shown; nothing else is."""


class ApprovalNeeded(Exception):
    def __init__(self, message: str, approver_role: str = "approver"):
        super().__init__(message)
        self.message, self.approver_role = message, approver_role


@dataclass
class User:
    id: str
    tenant: str = "default"
    roles: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tenant": self.tenant, "roles": list(self.roles)}

    def has(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


@dataclass
class Caller:
    """Who called the hub: the end user, plus their bearer token when the agent forwarded one (OIDC)."""
    user: User
    token: str | None = None
    client: str = ""


@dataclass
class ToolContext:
    user: User
    tool: str
    pack: str
    backend: Any
    settings: dict[str, Any]
    get_token: Callable[[], Awaitable[str | None]]
    approval: dict[str, Any] | None = None       # set when a held call is being run after approval
    trace_id: str = ""
    identity: str = ""                            # how the hub authenticates to the system (see identity.py)

    @property
    def acts_as_user(self) -> bool:
        """True when the system sees the end user's own identity (so it applies their permissions itself)."""
        return self.identity in ("token_exchange", "ema", "user_oauth", "fake")

    async def token(self) -> str | None:
        return await self.get_token()

    def require_approval(self, message: str, approver_role: str = "approver") -> None:
        """Hold this call for a human unless it is already the approved re-run. For data-dependent thresholds."""
        if self.approval is None:
            raise ApprovalNeeded(message, approver_role)


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]


@dataclass
class HubTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    pack: str = ""
    system: str = ""
    writes: bool = False
    title: str | None = None
    model: type[BaseModel] | None = None   # set for python tools; used to validate arguments

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.model is None:
            return _validate_schema(self.input_schema, args)
        try:
            return self.model(**args).model_dump(exclude_none=False)
        except ValidationError as e:
            msgs = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}" for err in e.errors())
            raise ToolError(f"invalid arguments: {msgs}") from None


def tool(_fn=None, *, writes: bool = False, name: str | None = None, title: str | None = None):
    def mark(fn):
        fn.__hub_tool__ = {"writes": writes, "name": name or fn.__name__, "title": title}
        return fn
    return mark(_fn) if _fn is not None else mark


def from_function(fn: Callable, pack: str = "", system: str = "") -> HubTool:
    meta = getattr(fn, "__hub_tool__", {"writes": False, "name": fn.__name__, "title": None})
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception as e:  # noqa: BLE001
        raise TypeError(f"tool {fn.__name__}: cannot resolve type hints ({e})") from None
    if not params or params[0].name != "ctx":
        raise TypeError(f"tool {fn.__name__}: the first parameter must be `ctx: ToolContext`")
    fields: dict[str, Any] = {}
    for p in params[1:]:
        if p.name not in hints:
            raise TypeError(f"tool {fn.__name__}: parameter {p.name} needs a type hint")
        fields[p.name] = (hints[p.name], ... if p.default is inspect.Parameter.empty else p.default)
    model = create_model(f"{fn.__name__}_args", __config__=ConfigDict(extra="forbid"), **fields)
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    doc = inspect.getdoc(fn) or ""
    if not doc:
        raise TypeError(f"tool {fn.__name__}: add a docstring; it is the description the model sees")

    async def handler(ctx: ToolContext, args: dict[str, Any]) -> Any:
        out = fn(ctx, **args)
        return await out if inspect.isawaitable(out) else out

    return HubTool(meta["name"], doc, schema, handler, pack, system, meta["writes"], meta["title"], model)


_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


def _validate_schema(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Minimal JSON-schema check for generated / proxied tools: required, types, enums, unknown keys."""
    if not isinstance(args, dict):
        raise ToolError("invalid arguments: expected an object")
    props = schema.get("properties", {})
    missing = [r for r in schema.get("required", []) if r not in args]
    if missing:
        raise ToolError(f"invalid arguments: missing {', '.join(missing)}")
    if schema.get("additionalProperties") is False or props:
        extra = [k for k in args if k not in props]
        if extra:
            raise ToolError(f"invalid arguments: unknown {', '.join(extra)}")
    for k, v in args.items():
        p = props.get(k, {})
        t = p.get("type")
        if isinstance(t, str) and t in _JSON_TYPES and v is not None:
            ok = isinstance(v, _JSON_TYPES[t]) and not (t in ("integer", "number") and isinstance(v, bool))
            if not ok:
                raise ToolError(f"invalid arguments: {k} should be {t}")
        if "enum" in p and v not in p["enum"]:
            raise ToolError(f"invalid arguments: {k} must be one of {p['enum']}")
    return args
