"""The service over real HTTP: MCP for both protocol eras, auth modes, and the REST API."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import httpx2
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

KEYH = {"X-API-Key": "k1"}


def serve(app) -> tuple[str, uvicorn.Server]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


@pytest.fixture
def api_key_app(monkeypatch, root):
    monkeypatch.setenv("HUB_AUTH", "api_key")
    monkeypatch.setenv("HUB_API_KEYS", "k1,k2")
    from connector_hub.server import create_app
    base, server = serve(create_app(root))
    yield base
    server.should_exit = True


def u(uid, roles=""):
    return {**KEYH, "X-End-User-Id": uid, "X-End-User-Roles": roles}


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_both_protocol_eras(api_key_app, mode):
    async def run():
        url = f"{api_key_app}/mcp/support-agent"
        async with Client(streamable_http_client(url, http_client=httpx2.AsyncClient(headers=u("cust-001"))), mode=mode) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            assert "get_case" in names and "get_incident" not in names
            tool = next(t for t in (await c.list_tools()).tools if t.name == "create_case")
            assert tool.annotations.destructive_hint and "ctx" not in tool.input_schema["properties"]
            r = await c.call_tool("get_case", {"case_number": "00001001"})
            assert not r.is_error and r.structured_content["status"] == "Working"
            r = await c.call_tool("get_case", {"case_number": "00001002"})
            assert r.is_error                                           # other customer's case
            r = await c.call_tool("update_case_status", {"case_number": "00001001", "status": "Closed"})
            assert not r.is_error and r.structured_content["status"] == "pending_approval"
            return c.protocol_version, r.structured_content["approval_id"]
    version, aid = asyncio.run(run())
    assert version == ("2026-07-28" if mode == "auto" else "2025-11-25")
    lead = u("lead-1", "support_lead")
    rows = httpx.get(f"{api_key_app}/v1/approvals", headers=lead).json()
    assert aid in [r["id"] for r in rows]
    assert httpx.get(f"{api_key_app}/v1/approvals", headers=u("x")).json() == []      # not an approver
    r = httpx.post(f"{api_key_app}/v1/approvals/{aid}", headers=lead, json={"approve": True, "reason": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert httpx.post(f"{api_key_app}/v1/approvals/{aid}", headers=lead, json={"approve": True}).status_code == 409


def test_auth_and_routing(api_key_app):
    assert httpx.post(f"{api_key_app}/mcp/support-agent", json={}).status_code == 401
    assert httpx.post(f"{api_key_app}/mcp/support-agent", json={}, headers=KEYH).status_code == 400   # no end user
    assert httpx.post(f"{api_key_app}/mcp/nope", json={}, headers=u("a")).status_code == 404
    assert httpx.get(f"{api_key_app}/healthz").json()["ok"]
    assert httpx.get(f"{api_key_app}/readyz").json()["ready"]
    assert httpx.get(f"{api_key_app}/").status_code == 404           # dev page only in dev
    assert httpx.post(f"{api_key_app}/v1/dev/reset").status_code in (404, 405)


def test_rest_admin_audit_erasure(api_key_app):
    r = httpx.post(f"{api_key_app}/v1/call/claims-agent/get_policy", headers=u("cust-001"),
                   json={"arguments": {"policy_number": "POL-1001"}})
    assert r.json()["status"] == "ok" and r.json()["result"]["status"] == "active"
    tools = httpx.get(f"{api_key_app}/v1/profiles/claims-agent/tools", headers=u("a")).json()
    assert {"get_policy", "open_claim"} <= {t["name"] for t in tools}
    assert httpx.get(f"{api_key_app}/v1/audit", headers=u("a")).status_code == 403
    audit = httpx.get(f"{api_key_app}/v1/audit", headers=u("aud", "hub_auditor")).json()
    assert audit and audit[0]["tool"] == "get_policy" and audit[0]["user_id"] == "cust-001"
    assert httpx.post(f"{api_key_app}/v1/admin/profiles/claims-agent/tools/get_policy/disable", headers=u("a")).status_code == 403
    r = httpx.post(f"{api_key_app}/v1/admin/profiles/claims-agent/tools/get_policy/disable", headers=u("adm", "hub_admin"))
    assert r.json()["disabled"]
    r = httpx.post(f"{api_key_app}/v1/call/claims-agent/get_policy", headers=u("cust-001"),
                   json={"arguments": {"policy_number": "POL-1001"}})
    assert r.json()["status"] == "denied"
    assert httpx.delete(f"{api_key_app}/v1/users/cust-001", headers=u("a")).status_code == 403
    assert httpx.delete(f"{api_key_app}/v1/users/cust-001", headers=u("p", "privacy_admin")).status_code == 200


def test_oidc_mode(monkeypatch, root):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJWKS:
        def __init__(self, *a, **k):
            pass

        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(jwt, "PyJWKClient", FakeJWKS)
    from connector_hub import auth
    auth._jwks.cache_clear()
    monkeypatch.setenv("HUB_AUTH", "oidc")
    monkeypatch.setenv("HUB_OIDC_ISSUER", "https://idp.example")
    monkeypatch.setenv("HUB_OIDC_AUDIENCE", "connector-hub")
    from connector_hub.server import create_app
    base, server = serve(create_app(root))
    try:
        r = httpx.post(f"{base}/mcp/support-agent", json={})
        assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
        meta = httpx.get(f"{base}/.well-known/oauth-protected-resource/mcp/support-agent").json()
        assert meta["authorization_servers"] == ["https://idp.example"] and meta["resource"].endswith("/mcp/support-agent")

        def tok(aud="connector-hub", exp=600):
            return jwt.encode({"sub": "cust-001", "aud": aud, "iss": "https://idp.example", "exp": int(time.time()) + exp,
                               "roles": ["customer"]}, key, algorithm="RS256")
        good = {"Authorization": f"Bearer {tok()}"}
        r = httpx.post(f"{base}/v1/call/support-agent/get_case", headers=good, json={"arguments": {"case_number": "00001001"}})
        assert r.json()["status"] == "ok"
        for bad in (tok(aud="other"), tok(exp=-60)):
            r = httpx.post(f"{base}/v1/call/support-agent/get_case", headers={"Authorization": f"Bearer {bad}"}, json={})
            assert r.status_code == 401
    finally:
        server.should_exit = True


def test_dev_mode_page_and_reset(monkeypatch, root):
    monkeypatch.setenv("HUB_AUTH", "dev")
    with pytest.raises(Exception, match="HUB_ENV=dev"):
        from connector_hub.server import create_app
        create_app(root)
    monkeypatch.setenv("HUB_ENV", "dev")
    base, server = serve(create_app(root))
    try:
        assert "connector-hub" in httpx.get(f"{base}/").text
        assert httpx.post(f"{base}/v1/dev/reset").json()["reset"] >= 4
    finally:
        server.should_exit = True


def test_prod_refuses_fakes(monkeypatch, root):
    monkeypatch.setenv("HUB_AUTH", "api_key")
    monkeypatch.setenv("HUB_API_KEYS", "k1")
    monkeypatch.setenv("HUB_ENV", "prod")
    from connector_hub.server import create_app
    base, server = serve(create_app(root))
    try:
        r = httpx.get(f"{base}/readyz")
        assert r.status_code == 503 and "fakes" in r.json()["error"]
        r = httpx.post(f"{base}/v1/call/support-agent/get_case", headers=u("cust-001"),
                       json={"arguments": {"case_number": "00001001"}})
        assert r.json()["status"] == "error"
    finally:
        server.should_exit = True
