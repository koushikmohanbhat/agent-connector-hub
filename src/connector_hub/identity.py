"""The identity broker: a credential for one system, for the end user the hub is acting for.

  fake             unsigned token naming the user, for fakes only (refused when HUB_ENV=prod)
  none             no credential
  service_account  a technical account: client credentials at token_url, or a static key from static_token_env.
                   The pack must filter results per user (auth.user_filter documents how); the audit log records the user.
  token_exchange   RFC 8693: the user's token (forwarded with HUB_AUTH=oidc) or a hub-signed assertion for the user,
                   exchanged at the IdP for a token with the system's audience and the tool's scopes.
  ema              MCP Enterprise-Managed Authorization: token exchange at the IdP for an ID-JAG
                   (requested_token_type id-jag, audience = the system's authorization server, resource = its MCP URL),
                   then a JWT-bearer grant (RFC 7523) at downstream_token_url for the access token.
  user_oauth       the user linked their account once (authorization code + PKCE, /v1/links/<system>/start); the hub
                   keeps the refresh token encrypted with HUB_LINK_KEY and refreshes at token_url.

Tokens are cached per tenant, user, system and scopes until shortly before they expire. They are never logged and
never shown to the model.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import fakes
from .config import AuthSpec
from .tools import Caller, ToolError

TE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ID_JAG = "urn:ietf:params:oauth:token-type:id-jag"
ACCESS = "urn:ietf:params:oauth:token-type:access_token"
JWT_TYPE = "urn:ietf:params:oauth:token-type:jwt"


class IdentityError(ToolError):
    pass


def _env(name: str, what: str) -> str:
    v = os.environ.get(name, "") if name else ""
    if not v:
        raise IdentityError(f"{what}: set {name or 'the *_env setting'} in the environment")
    return v


def _client_auth(auth: AuthSpec) -> dict[str, Any]:
    if not auth.client_id_env:
        return {}
    cid = _env(auth.client_id_env, "client id")
    secret = os.environ.get(auth.client_secret_env, "") if auth.client_secret_env else ""
    return {"auth": (cid, secret)} if secret else {"data": {"client_id": cid}}


def assertion_for(user: dict[str, Any], audience: str, actor: str | None = None) -> str:
    """A short-lived JWT, signed by the hub, naming the user (and the approver acting on a held call)."""
    import jwt
    key = os.environ.get("HUB_ASSERTION_KEY")
    if not key and os.environ.get("HUB_ASSERTION_KEY_FILE"):
        key = open(os.environ["HUB_ASSERTION_KEY_FILE"], encoding="utf-8").read()
    if not key:
        raise IdentityError("no user token to exchange: use HUB_AUTH=oidc so the agent forwards the user's token, "
                            "or set HUB_ASSERTION_KEY (a key the IdP trusts) so the hub can assert the user")
    now = int(time.time())
    claims = {"iss": os.environ.get("HUB_ASSERTION_ISSUER", "connector-hub"), "sub": user["id"], "aud": audience,
              "iat": now, "exp": now + 300, "jti": uuid.uuid4().hex, "tenant": user.get("tenant", "default")}
    if actor:
        claims["act"] = {"sub": actor}
    headers = {"kid": os.environ["HUB_ASSERTION_KID"]} if os.environ.get("HUB_ASSERTION_KID") else None
    return jwt.encode(claims, key, algorithm=os.environ.get("HUB_ASSERTION_ALG", "RS256"), headers=headers)


class IdentityBroker:
    def __init__(self, store=None, timeout: float = 15.0):
        self.store = store
        self.timeout = timeout
        self._cache: dict[tuple, tuple[str, float]] = {}

    async def token(self, auth: AuthSpec, system: str, caller: Caller, scopes: list[str], *,
                    actor: str | None = None) -> str | None:
        user = caller.user.as_dict()
        mode = auth.identity
        if mode == "none":
            return None
        if mode == "fake":
            return fakes.fake_token(user, system, scopes)
        key = (mode, system, "" if mode == "service_account" else f"{user['tenant']}/{user['id']}", " ".join(sorted(scopes)))
        hit = self._cache.get(key)
        if hit and hit[1] > time.time() + 30 and actor is None:
            return hit[0]
        if mode == "service_account":
            tok, ttl = await self._service(auth, scopes)
        elif mode == "token_exchange":
            tok, ttl = await self._exchange(auth, caller, scopes, actor)
        elif mode == "ema":
            tok, ttl = await self._ema(auth, caller, scopes, actor)
        elif mode == "user_oauth":
            tok, ttl = await self._linked(auth, system, user, scopes)
        else:  # pragma: no cover - validated in AuthSpec
            raise IdentityError(f"unknown identity mode {mode}")
        if actor is None:
            self._cache[key] = (tok, time.time() + ttl)
        return tok

    async def _post(self, url: str, data: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        if not url:
            raise IdentityError("token_url is not set for this system")
        data = {**data, **extra.get("data", {})}
        if fakes.is_fake(url):
            parts = urlsplit(url)
            async with fakes.http_client(f"fake://{parts.netloc}", timeout=self.timeout) as c:
                r = await c.post(parts.path or "/", data=data, auth=extra.get("auth"))
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(url, data=data, auth=extra.get("auth"))
        if r.status_code >= 400:
            detail = r.json().get("error", "") if r.headers.get("content-type", "").startswith("application/json") else ""
            raise IdentityError(f"token request was refused ({r.status_code} {detail})".strip())
        return r.json()

    async def _service(self, auth: AuthSpec, scopes: list[str]) -> tuple[str, float]:
        if auth.static_token_env:
            return _env(auth.static_token_env, "service account key"), 3600.0
        body = await self._post(auth.token_url, {"grant_type": "client_credentials", "scope": " ".join(scopes)},
                                _client_auth(auth))
        return body["access_token"], float(body.get("expires_in", 300))

    def _subject(self, auth: AuthSpec, caller: Caller, actor: str | None) -> tuple[str, str]:
        if caller.token and actor is None:
            return caller.token, auth.subject_token_type
        return assertion_for(caller.user.as_dict(), auth.token_url, actor), JWT_TYPE

    async def _exchange(self, auth: AuthSpec, caller: Caller, scopes: list[str], actor: str | None) -> tuple[str, float]:
        subject, stype = self._subject(auth, caller, actor)
        data = {"grant_type": TE, "subject_token": subject, "subject_token_type": stype,
                "requested_token_type": ACCESS, "scope": " ".join(scopes)}
        if auth.audience:
            data["audience"] = auth.audience
        body = await self._post(auth.token_url, data, _client_auth(auth))
        return body["access_token"], float(body.get("expires_in", 300))

    async def _ema(self, auth: AuthSpec, caller: Caller, scopes: list[str], actor: str | None) -> tuple[str, float]:
        subject, stype = self._subject(auth, caller, actor)
        jag = await self._post(auth.token_url, {"grant_type": TE, "requested_token_type": ID_JAG, "subject_token": subject,
                                                "subject_token_type": stype, "audience": auth.audience,
                                                "resource": auth.resource, "scope": " ".join(scopes)}, _client_auth(auth))
        if jag.get("issued_token_type") != ID_JAG:
            raise IdentityError("the IdP did not return an ID-JAG")
        body = await self._post(auth.downstream_token_url, {"grant_type": JWT_BEARER, "assertion": jag["access_token"]},
                                _client_auth(auth))
        return body["access_token"], float(body.get("expires_in", 300))

    async def _linked(self, auth: AuthSpec, system: str, user: dict[str, Any], scopes: list[str]) -> tuple[str, float]:
        secret = self.store.get_link(user["tenant"], user["id"], system) if self.store else None
        if not secret:
            raise IdentityError(f"connect your {system} account first: GET /v1/links/{system}/start")
        refresh = _fernet().decrypt(secret.encode()).decode()
        body = await self._post(auth.token_url, {"grant_type": "refresh_token", "refresh_token": refresh,
                                                 "scope": " ".join(scopes)}, _client_auth(auth))
        if body.get("refresh_token") and self.store:
            self.store.save_link(user["tenant"], user["id"], system, _fernet().encrypt(body["refresh_token"].encode()).decode())
        return body["access_token"], float(body.get("expires_in", 300))

    # account linking (user_oauth) -------------------------------------------------------------------------------
    def start_link(self, auth: AuthSpec, system: str, user: dict[str, Any], redirect_uri: str) -> tuple[str, dict]:
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(24)
        cid = _env(auth.client_id_env, "client id")
        q = httpx.QueryParams({"response_type": "code", "client_id": cid, "redirect_uri": redirect_uri, "state": state,
                               "code_challenge": challenge, "code_challenge_method": "S256",
                               "scope": " ".join(sorted({s for v in auth.scopes.values() for s in v}))})
        return f"{auth.authorize_url}?{q}", {"state": state, "verifier": verifier, "user": user, "system": system,
                                              "redirect_uri": redirect_uri, "expires": time.time() + 600}

    async def finish_link(self, auth: AuthSpec, pending: dict, code: str) -> None:
        body = await self._post(auth.token_url, {"grant_type": "authorization_code", "code": code,
                                                 "redirect_uri": pending["redirect_uri"],
                                                 "code_verifier": pending["verifier"]}, _client_auth(auth))
        if not body.get("refresh_token"):
            raise IdentityError("the system did not return a refresh token (check offline_access / refresh scopes)")
        u = pending["user"]
        self.store.save_link(u["tenant"], u["id"], pending["system"], _fernet().encrypt(body["refresh_token"].encode()).decode())


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("HUB_LINK_KEY")
    if not key:
        raise IdentityError("user_oauth needs HUB_LINK_KEY (a Fernet key from a secret manager) to encrypt refresh tokens")
    return Fernet(key.encode())
