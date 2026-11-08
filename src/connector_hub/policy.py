"""Tool policies: small sandboxed expressions evaluated before a tool runs (the same language as agent-starter-kit).

    {tool: open_claim, require_approval_if: "args.estimated_amount > 50000", approver_role: claims_supervisor}
    {tool: create_incident, deny_if: "'itil' not in user.roles and args.impact == 1"}
    {tool: "*", rate_limit: {per_user: "30/min"}}

Names available: `args` (the tool arguments), `user` (id, tenant, roles). Allowed: literals, comparisons, boolean
operators, arithmetic, `in`, attribute / key access on those two names, and len/lower/upper. Nothing else.
Rules that depend on data in the system (is the policy active?) belong in the tool, which can read the system.
"""
from __future__ import annotations

import ast
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from .config import PolicySpec

ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare, ast.Eq, ast.NotEq,
           ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.BinOp, ast.Add, ast.Sub, ast.Mult,
           ast.Div, ast.Mod, ast.Constant, ast.Name, ast.Load, ast.Attribute, ast.Subscript, ast.List, ast.Tuple, ast.Call)
FUNCS = {"len": len, "lower": lambda s: str(s).lower(), "upper": lambda s: str(s).upper()}
_UNITS = {"s": 1, "sec": 1, "min": 60, "m": 60, "hour": 3600, "h": 3600, "day": 86400, "d": 86400}


class PolicyError(ValueError):
    pass


class _NS:
    """Attribute and key access over a dict; missing keys are None (so `args.amount > 5000` with no amount is False).
    Not a dict subclass, so argument names like `items` or `keys` are not shadowed by dict methods."""
    __slots__ = ("_d",)

    def __init__(self, d: dict):
        object.__setattr__(self, "_d", d)

    def __getattr__(self, k):
        v = self._d.get(k)
        return _NS(v) if isinstance(v, dict) else v

    __getitem__ = __getattr__

    def __contains__(self, k) -> bool:
        return k in self._d

    def __len__(self) -> int:
        return len(self._d)


def compile_rule(rule: str):
    try:
        tree = ast.parse(rule, mode="eval")
    except SyntaxError as e:
        raise PolicyError(f"policy rule {rule!r}: {e.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED):
            raise PolicyError(f"policy rule {rule!r}: {type(node).__name__} is not allowed")
        if isinstance(node, ast.Name) and node.id not in ("args", "user", *FUNCS, "True", "False", "None"):
            raise PolicyError(f"policy rule {rule!r}: unknown name {node.id!r} (use args.* or user.*)")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise PolicyError(f"policy rule {rule!r}: private attributes are not allowed")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in FUNCS):
            raise PolicyError(f"policy rule {rule!r}: only {', '.join(FUNCS)} may be called")
    return compile(tree, "<policy>", "eval")


def parse_rate(rate: str) -> tuple[int, float]:
    n, _, unit = rate.partition("/")
    try:
        return int(n), float(_UNITS[unit.strip().lower()])
    except (ValueError, KeyError):
        raise PolicyError(f"rate limit {rate!r}: expected e.g. 30/min, 1000/hour") from None


def _num(v):
    try:
        return float(v) if isinstance(v, str) and v.strip() else v
    except ValueError:
        return v


@dataclass
class Decision:
    action: str                  # allow | deny | require_approval | rate_limited
    policy: PolicySpec | None = None
    message: str = ""


class RateLimiter:
    def __init__(self):
        self._hits: dict[tuple, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: tuple, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


class PolicySet:
    def __init__(self, specs: list[PolicySpec]):
        self.specs = list(specs)
        self._code = {(i, k): compile_rule(getattr(s, k)) for i, s in enumerate(self.specs)
                      for k in ("deny_if", "require_approval_if") if getattr(s, k)}
        for s in self.specs:
            for r in (s.rate_limit or {}).values():
                parse_rate(r)
        self.limiter = RateLimiter()

    def evaluate(self, tool: str, args: dict[str, Any], user: dict[str, Any], scope: str = "") -> Decision:
        ns = {"args": _NS({k: _num(v) for k, v in args.items()}), "user": _NS(user), **FUNCS, "__builtins__": {}}
        approval = None
        for i, s in enumerate(self.specs):
            if s.tool not in ("*", tool):
                continue
            for key in ("deny_if", "require_approval_if"):
                code = self._code.get((i, key))
                if code is None:
                    continue
                try:
                    hit = bool(eval(code, ns))  # noqa: S307 - AST-whitelisted, no builtins
                except TypeError:
                    hit = False
                if hit and key == "deny_if":
                    return Decision("deny", s, s.message or f"{tool} is not allowed here")
                if hit and approval is None:
                    approval = Decision("require_approval", s, s.message or f"{tool} needs approval from {s.approver_role}")
        for i, s in enumerate(self.specs):
            if s.tool not in ("*", tool) or not s.rate_limit:
                continue
            for kind, rate in s.rate_limit.items():
                limit, window = parse_rate(rate)
                who = user.get("id") if kind == "per_user" else user.get("tenant")
                if not self.limiter.hit((scope, i, kind, who, tool if s.tool != "*" else "*"), limit, window):
                    return Decision("rate_limited", s, f"rate limit {rate} reached for {tool}")
        return approval or Decision("allow")
