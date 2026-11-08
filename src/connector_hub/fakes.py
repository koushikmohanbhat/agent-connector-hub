"""Offline stand-ins. A pack's settings can point at `fake://<host>` instead of a real endpoint; the hub then routes
HTTP calls to an in-process ASGI app and MCP calls to an in-process MCP server registered under that host. The same
backend code runs against the fake and the real system, so a fake run exercises the real request path.

Fake credentials (`identity: fake`) are unsigned tokens naming the user, so fakes can enforce per-user access the way
the real systems do. Both are refused when HUB_ENV=prod.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

_FAKES: dict[str, Any] = {}


class FakeNotAllowed(RuntimeError):
    pass


def fakes_allowed() -> bool:
    return os.environ.get("HUB_ENV", "").lower() != "prod"


def register(host: str, target: Any) -> None:
    _FAKES[host] = target


def registered() -> dict[str, Any]:
    return dict(_FAKES)


def is_fake(url: str | None) -> bool:
    return bool(url) and str(url).startswith("fake://")


def resolve(url: str) -> tuple[Any, str]:
    """fake://host/path -> (registered target, path)."""
    if not fakes_allowed():
        raise FakeNotAllowed(f"{url}: fake endpoints are refused when HUB_ENV=prod")
    parts = urlsplit(url)
    if parts.netloc not in _FAKES:
        raise KeyError(f"{url}: no fake registered for {parts.netloc!r}")
    return _FAKES[parts.netloc], parts.path or "/"


def http_client(base_url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> httpx.AsyncClient:
    if is_fake(base_url):
        app, path = resolve(base_url)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake" + path.rstrip("/"),
                                 headers=headers, timeout=timeout)
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)


def fake_token(user: dict[str, Any], system: str, scopes: list[str]) -> str:
    if not fakes_allowed():
        raise FakeNotAllowed("identity: fake is refused when HUB_ENV=prod")
    body = {"sub": user["id"], "tenant": user.get("tenant", "default"), "roles": user.get("roles", []), "aud": system,
            "scope": " ".join(scopes)}
    return "fake." + base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")


def read_fake_token(authorization: str | None) -> dict[str, Any] | None:
    """For fakes: the claims in a fake bearer token, or None."""
    if not authorization:
        return None
    tok = authorization.split(" ", 1)[-1]
    if not tok.startswith("fake."):
        return None
    raw = tok[5:]
    try:
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        return None
