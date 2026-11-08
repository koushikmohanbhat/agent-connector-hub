"""The hub: packs and profiles loaded once; every tool call goes through the same pipeline.

    profile allowlist -> argument schema -> writes allowed? -> policies (pack, then profile) -> rate limits
      -> identity (a credential for this user and system) -> tool -> on_result hook -> tokenise (if the profile asks)
      -> audit

A call can end four ways: ok, error (shown to the model), denied, or pending_approval (held for a human). Approved
calls run later with the original user's identity, with the approver recorded as the actor.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import tokenize as tk
from . import upstream
from .config import AuthSpec, ConfigError, HubSpec, ProfileSpec, load_hub, read_yaml
from .identity import IdentityBroker
from .packs import Pack, discover, import_ref
from .policy import PolicySet
from .store import Store
from .telemetry import span, trace_id
from .tools import ApprovalNeeded, Caller, HubTool, ToolContext, ToolError, User

log = logging.getLogger("connector_hub")


class ApprovalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Exposed:
    name: str
    tool: HubTool
    auth: AuthSpec
    pack_policies: PolicySet
    settings: dict[str, Any] = field(default_factory=dict)
    backend: Any = None


@dataclass
class Outcome:
    status: str                       # ok | error | denied | pending_approval
    value: Any = None
    message: str = ""
    approval_id: str | None = None
    audit_id: str = ""
    trace_id: str = ""

    def payload(self) -> Any:
        if self.status == "ok":
            return self.value
        if self.status == "pending_approval":
            return {"status": "pending_approval", "approval_id": self.approval_id, "message": self.message}
        return {"status": self.status, "message": self.message}


class Hub:
    def __init__(self, spec: HubSpec, store: Store | None = None, profiles: list[ProfileSpec] | None = None):
        self.spec = spec
        base = spec.base_dir
        self.store = store or Store(os.environ.get("HUB_STORE") or spec.store)
        self.packs: dict[str, Pack] = discover(base / spec.packs_dir, spec.packs or None)
        self.profiles: dict[str, ProfileSpec] = {}
        pdir = base / spec.profiles_dir
        for f in sorted(pdir.glob("*.yaml")) if pdir.is_dir() and profiles is None else []:
            p = ProfileSpec(**read_yaml(f))
            self.profiles[p.name] = p
        for p in profiles or []:
            self.profiles[p.name] = p
        self.broker = IdentityBroker(self.store)
        self.disabled: set[tuple[str, str]] = set()
        self._toolsets: dict[str, dict[str, Exposed]] = {}
        self._profile_policies = {n: PolicySet(p.policies) for n, p in self.profiles.items()}
        self._pack_policies = {n: PolicySet(p.spec.policies) for n, p in self.packs.items()}
        self.hooks = {k: import_ref(v, base) for k, v in spec.hooks.items()}
        self._lock = asyncio.Lock()
        for name in self.profiles:
            self._check_profile(name)

    @classmethod
    def from_path(cls, path: str | Path, store: Store | None = None) -> Hub:
        return cls(load_hub(path), store)

    def _check_profile(self, name: str) -> None:
        for ref in self.profiles[name].tools:
            pack, _, tool = ref.partition(":")
            if pack not in self.packs:
                raise ConfigError(f"profile {name}: pack {pack!r} is not loaded")
            if tool != "*" and tool not in self.packs[pack].tools:
                raise ConfigError(f"profile {name}: pack {pack} has no tool {tool!r} "
                                  f"(it has: {', '.join(self.packs[pack].tools)})")

    async def toolset(self, profile: str) -> dict[str, Exposed]:
        if profile in self._toolsets:
            return self._toolsets[profile]
        if profile not in self.profiles:
            raise KeyError(profile)
        async with self._lock:
            if profile in self._toolsets:
                return self._toolsets[profile]
            prof = self.profiles[profile]
            out: dict[str, Exposed] = {}
            for ref in prof.tools:
                pname, _, tname = ref.partition(":")
                pack = self.packs[pname]
                for t in (pack.tools.values() if tname == "*" else [pack.tools[tname]]):
                    exposed = prof.rename.get(f"{pname}:{t.name}", t.name)
                    if exposed in out:
                        raise ConfigError(f"profile {profile}: two tools are called {exposed!r}; use rename")
                    out[exposed] = Exposed(exposed, t, pack.spec.auth, self._pack_policies[pname], pack.spec.settings,
                                           pack.backend)
            for px in prof.proxy:
                listing = {t["name"]: t for t in await upstream.list_tools(px.url, None)}
                for t in upstream.proxy_tools(px, listing):
                    if t.name in out:
                        raise ConfigError(f"profile {profile}: two tools are called {t.name!r}")
                    out[t.name] = Exposed(t.name, t, px.auth, PolicySet([]))
            self._toolsets[profile] = out
            return out

    # the pipeline ---------------------------------------------------------------------------------------------------
    async def call(self, profile: str, name: str, args: dict[str, Any] | None, caller: Caller, *,
                   approval: dict[str, Any] | None = None) -> Outcome:
        t0 = time.perf_counter()
        tid = trace_id()
        prof = self.profiles.get(profile)
        if prof is None:
            return Outcome("error", message=f"unknown profile {profile!r}", trace_id=tid)
        ts = await self.toolset(profile)
        ex = ts.get(name)
        user = caller.user.as_dict()
        audit = {"tenant": caller.user.tenant, "profile": profile, "client": caller.client, "user_id": caller.user.id,
                 "tool": name, "trace_id": tid, "args": tk.tokenize(args or {}),
                 "approval_id": (approval or {}).get("id"),
                 "acting_for": (approval or {}).get("approver")}
        if ex is None:
            return self._finish(Outcome("error", message=f"unknown tool {name!r}"), audit, t0, "unknown_tool")
        audit.update(pack=ex.tool.pack, system=ex.tool.system)
        if (profile, name) in self.disabled:
            return self._finish(Outcome("denied", message=f"{name} is switched off by an administrator"), audit, t0,
                                "disabled")
        try:
            clean = ex.tool.validate(dict(args or {}))
        except ToolError as e:
            return self._finish(Outcome("error", message=str(e)), audit, t0, "invalid")
        if ex.tool.writes and not prof.allow_writes:
            return self._finish(Outcome("denied", message=f"{name} changes data and this profile is read-only"),
                                audit, t0, "read_only")
        if approval is None:
            decisions = [ex.pack_policies.evaluate(ex.tool.name, clean, user, f"{profile}:pack"),
                         self._profile_policies[profile].evaluate(name, clean, user, profile)]
            for decision in decisions:
                if decision.action in ("deny", "rate_limited"):
                    return self._finish(Outcome("denied", message=decision.message), audit, t0, decision.action)
            held = next((d for d in decisions if d.action == "require_approval"), None)
            if held is not None:
                return self._hold(profile, name, clean, caller, held.policy.approver_role, held.message, audit, t0)

        actor = (approval or {}).get("approver")

        async def get_token() -> str | None:
            return await self.broker.token(ex.auth, ex.tool.system, caller, ex.auth.scopes_for(ex.tool.name), actor=actor)

        ctx = ToolContext(caller.user, name, ex.tool.pack, ex.backend, ex.settings, get_token, approval, tid,
                          ex.auth.identity)
        attrs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name, "hub.profile": profile,
                 "hub.pack": ex.tool.pack, "hub.system": ex.tool.system, "enduser.id": caller.user.id,
                 "hub.approval_id": (approval or {}).get("id")}
        with span(f"execute_tool {name}", attrs):
            try:
                value = await asyncio.wait_for(ex.tool.handler(ctx, clean), timeout=prof.timeout_s)
            except ApprovalNeeded as e:
                if approval is not None:
                    return self._finish(Outcome("error", message="the tool asked for approval again"), audit, t0, "error")
                return self._hold(profile, name, clean, caller, e.approver_role, e.message, audit, t0)
            except ToolError as e:
                return self._finish(Outcome("error", message=str(e)), audit, t0, "allow", error=str(e))
            except TimeoutError:
                return self._finish(Outcome("error", message=f"{name} timed out"), audit, t0, "allow", error="timeout")
            except Exception as e:  # noqa: BLE001 - never leak internals to the model
                log.exception("tool %s failed (trace %s)", name, tid)
                return self._finish(Outcome("error", message=f"{name} failed; reference {tid}"), audit, t0, "allow",
                                    error=type(e).__name__)
        if "on_result" in self.hooks:
            value = self.hooks["on_result"](value, {"profile": profile, "tool": name, "user": user,
                                                    "system": ex.tool.system})
        if prof.tokenize_results:
            value = tk.tokenize(value)
        return self._finish(Outcome("ok", value), audit, t0, "allow")

    def _hold(self, profile, name, args, caller: Caller, role: str, message: str, audit, t0) -> Outcome:
        aid = self.store.hold(tenant=caller.user.tenant, profile=profile, tool=name, args=args,
                              user=caller.user.as_dict(), approver_role=role, message=message)
        audit["approval_id"] = aid
        return self._finish(Outcome("pending_approval", message=message, approval_id=aid), audit, t0, "require_approval")

    def _finish(self, out: Outcome, audit: dict, t0: float, decision: str, error: str | None = None) -> Outcome:
        out.trace_id = audit["trace_id"]
        status = {"ok": "ok", "pending_approval": "held"}.get(out.status, out.status)
        row = {**audit, "decision": decision, "status": status, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
               "error": error or (out.message if out.status in ("error", "denied") else None)}
        out.audit_id = self.store.record(**row)
        if "on_audit" in self.hooks:
            self.hooks["on_audit"](row)
        return out

    # approvals ------------------------------------------------------------------------------------------------------
    async def decide(self, approval_id: str, approve: bool, approver: User, reason: str = "") -> dict[str, Any]:
        row = self.store.get_approval(approval_id)
        if row is None or row["tenant"] != approver.tenant:
            raise ApprovalError(404, "approval not found")
        if not approver.has(row["approver_role"]):
            raise ApprovalError(403, f"needs the {row['approver_role']} role")
        if row["user"]["id"] == approver.id:
            raise ApprovalError(403, "you cannot approve your own request")
        if not self.store.decide(approval_id, "approved" if approve else "rejected", approver.id, reason):
            raise ApprovalError(409, f"already {row['status']}")
        if not approve:
            self.store.set_result(approval_id, "rejected", {"status": "rejected", "reason": reason})
            return {"approval_id": approval_id, "status": "rejected", "reason": reason}
        u = row["user"]
        caller = Caller(User(u["id"], u.get("tenant", "default"), u.get("roles", [])), None, "approval")
        out = await self.call(row["profile"], row["tool"], row["args"], caller,
                              approval={"id": approval_id, "approver": approver.id, "reason": reason})
        status = "executed" if out.status == "ok" else "failed"
        self.store.set_result(approval_id, status, out.payload())
        return {"approval_id": approval_id, "status": status, "result": out.payload()}

    def erase_user(self, tenant: str, user_id: str) -> int:
        return self.store.delete_user(tenant, user_id)
