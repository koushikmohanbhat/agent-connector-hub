"""The identity broker against a fake IdP / authorization server (registered as fake://idp and fake://sys-as)."""
from __future__ import annotations

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from connector_hub import fakes
from connector_hub.config import AuthSpec
from connector_hub.identity import ID_JAG, JWT_BEARER, TE, IdentityBroker, IdentityError
from connector_hub.store import Store

from .conftest import caller

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
SEEN: list[dict] = []


def idp_app() -> FastAPI:
    app = FastAPI()

    @app.post("/token")
    async def token(request: Request):
        form = dict(await request.form())
        SEEN.append(form)
        g = form.get("grant_type")
        if g == TE and form.get("requested_token_type") == ID_JAG:
            return {"access_token": "jag-for-" + _sub(form), "issued_token_type": ID_JAG, "token_type": "N_A",
                    "expires_in": 300}
        if g == TE:
            if form.get("subject_token") == "bad":
                return JSONResponse({"error": "invalid_grant"}, 400)
            return {"access_token": f"sys-{_sub(form)}-{form.get('scope')}", "expires_in": 600}
        if g == "client_credentials":
            return {"access_token": "svc-token", "expires_in": 600}
        if g == "refresh_token":
            return {"access_token": f"linked-{form['refresh_token']}", "refresh_token": "r2", "expires_in": 600}
        if g == "authorization_code":
            return {"access_token": "a", "refresh_token": "r1", "expires_in": 600}
        return JSONResponse({"error": "unsupported_grant_type"}, 400)
    return app


def as_app() -> FastAPI:
    app = FastAPI()

    @app.post("/token")
    async def token(request: Request):
        form = dict(await request.form())
        SEEN.append(form)
        assert form["grant_type"] == JWT_BEARER
        return {"access_token": "mcp-" + form["assertion"], "expires_in": 600}
    return app


def _sub(form) -> str:
    st = form.get("subject_token", "")
    if st.count(".") == 2:
        return jwt.decode(st, options={"verify_signature": False})["sub"]
    return st


@pytest.fixture(autouse=True)
def idp():
    SEEN.clear()
    fakes.register("idp", idp_app())
    fakes.register("sys-as", as_app())


async def test_token_exchange_with_forwarded_user_token():
    b = IdentityBroker()
    auth = AuthSpec(identity="token_exchange", token_url="fake://idp/token", audience="sys", scopes={"*": ["read"]})
    tok = await b.token(auth, "sys", caller(token="user-jwt"), ["read"])
    assert tok == "sys-user-jwt-read"
    assert SEEN[-1]["audience"] == "sys" and SEEN[-1]["subject_token"] == "user-jwt"
    await b.token(auth, "sys", caller(token="user-jwt"), ["read"])
    assert len(SEEN) == 1                                           # cached


async def test_token_exchange_with_hub_assertion(monkeypatch):
    b = IdentityBroker()
    auth = AuthSpec(identity="token_exchange", token_url="fake://idp/token")
    with pytest.raises(IdentityError, match="HUB_ASSERTION_KEY"):
        await b.token(auth, "sys", caller("u1"), [])
    monkeypatch.setenv("HUB_ASSERTION_KEY", PEM)
    tok = await b.token(auth, "sys", caller("u1"), [])
    assert tok.startswith("sys-u1")
    claims = jwt.decode(SEEN[-1]["subject_token"], KEY.public_key(), algorithms=["RS256"], audience="fake://idp/token")
    assert claims["sub"] == "u1" and "act" not in claims
    await b.token(auth, "sys", caller("u1"), [], actor="lead-7")       # approved re-run: approver is the actor
    claims = jwt.decode(SEEN[-1]["subject_token"], KEY.public_key(), algorithms=["RS256"], audience="fake://idp/token")
    assert claims["act"] == {"sub": "lead-7"}


async def test_refused_exchange_is_a_tool_error():
    auth = AuthSpec(identity="token_exchange", token_url="fake://idp/token")
    with pytest.raises(IdentityError, match="refused"):
        await IdentityBroker().token(auth, "sys", caller(token="bad"), [])


async def test_enterprise_managed_authorization():
    auth = AuthSpec(identity="ema", token_url="fake://idp/token", audience="https://sys-as", resource="https://sys/mcp",
                    downstream_token_url="fake://sys-as/token")
    tok = await IdentityBroker().token(auth, "sys", caller(token="idtoken"), ["x"])
    assert tok == "mcp-jag-for-idtoken"
    assert SEEN[0]["requested_token_type"] == ID_JAG and SEEN[0]["resource"] == "https://sys/mcp"


async def test_service_account(monkeypatch):
    monkeypatch.setenv("C_ID", "cid")
    monkeypatch.setenv("C_SECRET", "sec")
    auth = AuthSpec(identity="service_account", token_url="fake://idp/token", client_id_env="C_ID", client_secret_env="C_SECRET")
    b = IdentityBroker()
    assert await b.token(auth, "sys", caller("a"), []) == "svc-token"
    assert await b.token(auth, "sys", caller("b"), []) == "svc-token"
    assert len(SEEN) == 1                                           # one token for the account, not per user
    monkeypatch.setenv("STATIC", "key-1")
    assert await b.token(AuthSpec(identity="service_account", static_token_env="STATIC"), "s2", caller(), []) == "key-1"


async def test_user_oauth_linking(monkeypatch):
    monkeypatch.setenv("HUB_LINK_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("C_ID", "cid")
    store = Store("sqlite://")
    b = IdentityBroker(store)
    auth = AuthSpec(identity="user_oauth", token_url="fake://idp/token", authorize_url="https://sys/authorize",
                    client_id_env="C_ID", scopes={"*": ["api"]})
    with pytest.raises(IdentityError, match="connect your sys account"):
        await b.token(auth, "sys", caller("u1"), [])
    url, pending = b.start_link(auth, "sys", caller("u1").user.as_dict(), "https://hub/v1/links/callback")
    assert "code_challenge_method=S256" in url and pending["verifier"]
    await b.finish_link(auth, pending, "code-1")
    assert "r1" not in (store.get_link("default", "u1", "sys") or "")      # encrypted at rest
    assert await b.token(auth, "sys", caller("u1"), []) == "linked-r1"


async def test_fake_identity_refused_in_prod(monkeypatch):
    monkeypatch.setenv("HUB_ENV", "prod")
    with pytest.raises(fakes.FakeNotAllowed):
        await IdentityBroker().token(AuthSpec(identity="fake"), "s", caller(), [])
