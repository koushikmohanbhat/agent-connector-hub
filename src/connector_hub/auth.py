"""Who is calling the hub, and for which end user. Set HUB_AUTH:

  oidc     Bearer JWT from the customer's identity provider: the end user's token, forwarded by the agent. This is
           the mode that lets the hub do per-user token exchange with the user's own token.
           HUB_OIDC_ISSUER, HUB_OIDC_AUDIENCE, optional HUB_OIDC_JWKS_URL (default <issuer>/.well-known/jwks.json),
           HUB_OIDC_ROLES_CLAIM (default roles), HUB_OIDC_TENANT_CLAIM (default tenant).
  api_key  A trusted agent backend calls with X-API-Key and names the end user in X-End-User-Id (+ X-End-User-Roles,
           X-Tenant). Keys in HUB_API_KEYS (comma-separated; use a secret manager). Downstream token exchange then
           uses a hub-signed assertion for that user (HUB_ASSERTION_KEY), which the IdP must trust.
  dev      Local only: user from X-User-Id / X-User-Roles / X-Tenant headers, no checks. Refused unless HUB_ENV=dev.
"""
from __future__ import annotations

import hmac
import os
from functools import lru_cache

from .tools import Caller, User


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class AuthConfigError(RuntimeError):
    pass


def mode() -> str:
    m = os.environ.get("HUB_AUTH", "").lower()
    if not m:
        raise AuthConfigError("set HUB_AUTH to oidc, api_key or dev (dev needs HUB_ENV=dev)")
    if m not in ("oidc", "api_key", "dev"):
        raise AuthConfigError(f"HUB_AUTH={m!r}: expected oidc, api_key or dev")
    if m == "dev" and os.environ.get("HUB_ENV", "").lower() != "dev":
        raise AuthConfigError("HUB_AUTH=dev is only allowed with HUB_ENV=dev (it trusts any caller)")
    if m == "api_key" and not os.environ.get("HUB_API_KEYS"):
        raise AuthConfigError("HUB_AUTH=api_key needs HUB_API_KEYS")
    if m == "oidc" and not (os.environ.get("HUB_OIDC_ISSUER") and os.environ.get("HUB_OIDC_AUDIENCE")):
        raise AuthConfigError("HUB_AUTH=oidc needs HUB_OIDC_ISSUER and HUB_OIDC_AUDIENCE")
    return m


def _roles(v: str | None) -> list[str]:
    return [r.strip() for r in (v or "").split(",") if r.strip()]


@lru_cache(maxsize=4)
def _jwks(url: str):
    import jwt
    return jwt.PyJWKClient(url, cache_keys=True)


def authenticate(headers) -> Caller:
    m = mode()
    h = {k.lower(): v for k, v in headers.items()}
    client = h.get("user-agent", "")[:200]
    if m == "dev":
        return Caller(User(h.get("x-user-id", "dev-user"), h.get("x-tenant", "default"), _roles(h.get("x-user-roles"))),
                      None, client)
    if m == "api_key":
        key = h.get("x-api-key", "")
        if not any(hmac.compare_digest(key, k.strip()) for k in os.environ["HUB_API_KEYS"].split(",") if k.strip()):
            raise AuthError(401, "invalid API key")
        uid = h.get("x-end-user-id")
        if not uid:
            raise AuthError(400, "X-End-User-Id is required")
        return Caller(User(uid, h.get("x-tenant", "default"), _roles(h.get("x-end-user-roles"))), None, client)
    import jwt
    auth = h.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AuthError(401, "bearer token required")
    token = auth[7:]
    iss = os.environ["HUB_OIDC_ISSUER"].rstrip("/")
    try:
        key = _jwks(os.environ.get("HUB_OIDC_JWKS_URL") or f"{iss}/.well-known/jwks.json").get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256", "ES256", "PS256"], audience=os.environ["HUB_OIDC_AUDIENCE"],
                            issuer=iss, options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as e:
        raise AuthError(401, f"invalid token: {e}") from None
    roles = claims.get(os.environ.get("HUB_OIDC_ROLES_CLAIM", "roles"), [])
    roles = roles.split() if isinstance(roles, str) else list(roles)
    tenant = str(claims.get(os.environ.get("HUB_OIDC_TENANT_CLAIM", "tenant"), "default"))
    return Caller(User(str(claims["sub"]), tenant, roles, claims), token, client)
