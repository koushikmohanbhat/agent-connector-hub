"""Conformance checks every pack must pass (run offline against its fakes, in CI and with `hub conformance`).

  descriptions   every tool has a real description (the model chooses tools by it)
  schemas        argument schemas are objects with typed properties; `ctx` never appears
  writes         tools whose names say they change data are marked writes=True
  bad_input      unknown and missing arguments come back as tool errors, never crashes
  calls          the pack's conformance.yaml calls give the expected outcome (incl. other users' records hidden)
  parity         packs with several backends (e.g. mcp and rest) give the same results for every read call
  identity       a pack that acts through a technical account documents how it filters per user (auth.user_filter)

`hub record` runs the same calls against a live system and saves tokenised results for review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import fakes
from .config import HubSpec, ProfileSpec, read_yaml
from .engine import Hub
from .packs import import_ref
from .store import Store
from .tokenize import tokenize
from .tools import Caller, User

WRITE_WORDS = ("create_", "add_", "update_", "open_", "upload_", "delete_", "set_", "close_", "cancel_", "submit_")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    pack: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))


def reset_fakes() -> None:
    for target in fakes.registered().values():
        r = getattr(target, "reset", None) or getattr(getattr(target, "state", None), "reset", None)
        if callable(r):
            r()


def _hub_for(base: Path, pack: str) -> Hub:
    spec = HubSpec(packs_dir="packs", profiles_dir="__none__", packs=[pack], store="sqlite://", base_dir=base)
    return Hub(spec, Store("sqlite://"), profiles=[ProfileSpec(name="conformance", tools=[f"{pack}:*"])])


def _caller(u: dict[str, Any]) -> Caller:
    return Caller(User(u.get("id", "conformance-user"), u.get("tenant", "default"), list(u.get("roles", [])),
                       dict(u.get("claims", {}))))


def _norm(v: Any) -> Any:
    return json.loads(json.dumps(v, sort_keys=True, default=str))


async def check_pack(base: Path, pack: str) -> Report:
    rep = Report(pack)
    hub = _hub_for(base, pack)
    p = hub.packs[pack]
    tools = await hub.toolset("conformance")
    bad = [n for n, e in tools.items() if len(e.tool.description.strip()) < 20]
    rep.add("descriptions", not bad, ", ".join(bad))
    bad = []
    for n, e in tools.items():
        s = e.tool.input_schema
        if s.get("type") != "object" or "ctx" in s.get("properties", {}):
            bad.append(n)
            continue
        for k, prop in s.get("properties", {}).items():
            if not any(x in prop for x in ("type", "enum", "anyOf", "oneOf", "$ref", "allOf", "const", "format")):
                bad.append(f"{n}.{k}")
    rep.add("schemas", not bad, ", ".join(bad))
    bad = [n for n, e in tools.items() if n.startswith(WRITE_WORDS) and not e.tool.writes]
    rep.add("writes", not bad, ", ".join(bad))
    crash = []
    u = _caller({"id": "conformance-user"})
    for n, e in tools.items():
        out = await hub.call("conformance", n, {"__unknown__": 1}, u)
        if out.status != "error":
            crash.append(f"{n}: unknown argument accepted")
        if e.tool.input_schema.get("required"):
            out = await hub.call("conformance", n, {}, u)
            if out.status != "error":
                crash.append(f"{n}: missing arguments accepted")
    rep.add("bad_input", not crash, "; ".join(crash))
    if p.spec.auth.identity == "service_account":
        rep.add("identity", bool(p.spec.auth.user_filter), "auth.user_filter is required for service accounts")
    else:
        rep.add("identity", True, p.spec.auth.identity)

    cfile = p.dir / "conformance.yaml"
    calls = (read_yaml(cfile).get("calls") or []) if cfile.is_file() else []
    if not calls:
        rep.add("calls", False, "conformance.yaml has no calls")
        return rep
    reset_fakes()
    fails = []
    for i, c in enumerate(calls):
        out = await hub.call("conformance", c["tool"], c.get("args", {}), _caller(c.get("user", {})))
        exp = c.get("expect", {"status": "ok"})
        if out.status != exp.get("status", "ok"):
            fails.append(f"#{i} {c['tool']}: expected {exp.get('status', 'ok')}, got {out.status} ({out.message})")
            continue
        missing = [k for k in exp.get("keys", []) if not isinstance(out.value, dict) or k not in out.value]
        if missing:
            fails.append(f"#{i} {c['tool']}: result is missing {', '.join(missing)}")
        for k, v in (exp.get("equals") or {}).items():
            if not isinstance(out.value, dict) or out.value.get(k) != v:
                fails.append(f"#{i} {c['tool']}: {k} = {out.value.get(k) if isinstance(out.value, dict) else None!r}, "
                             f"expected {v!r}")
    rep.add("calls", not fails, "; ".join(fails) or f"{len(calls)} calls")

    if p.spec.kind == "python" and len(p.spec.backends) > 1:
        reads = [c for c in calls if not tools[c["tool"]].tool.writes]
        results: dict[str, list] = {}
        for bname, ref in p.spec.backends.items():
            p.backend = import_ref(ref, p.dir)(p.spec.settings, p.dir)
            hub._toolsets.clear()
            reset_fakes()
            got = []
            for c in reads:
                out = await hub.call("conformance", c["tool"], c.get("args", {}), _caller(c.get("user", {})))
                got.append(_norm({"status": out.status, "value": out.value}))
            results[bname] = got
        names = list(results)
        diff = [f"{names[0]} vs {other}: call #{i}" for other in names[1:]
                for i, (a, b) in enumerate(zip(results[names[0]], results[other], strict=True)) if a != b]
        rep.add("parity", not diff, "; ".join(diff) or f"{len(reads)} reads agree on {', '.join(names)}")
    reset_fakes()
    return rep


async def record(base: Path, pack: str, out_path: Path, user: dict[str, Any]) -> int:
    """Run the pack's read calls against whatever the environment points at, and save tokenised results."""
    hub = _hub_for(base, pack)
    p = hub.packs[pack]
    tools = await hub.toolset("conformance")
    calls = read_yaml(p.dir / "conformance.yaml").get("calls") or []
    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for c in calls:
            if tools[c["tool"]].tool.writes:
                continue
            out = await hub.call("conformance", c["tool"], c.get("args", {}), _caller(user))
            f.write(json.dumps(tokenize({"tool": c["tool"], "args": c.get("args", {}), "status": out.status,
                                         "result": out.payload()}), default=str) + "\n")
            n += 1
    return n
