"""hub.yaml, pack.yaml and profile files. Values may use ${VAR} / ${VAR:-default} from the environment (and a .env
file), so everything can be committed without environment-specific settings. Inline secrets are rejected: name the
environment variable that holds them instead (`client_secret_env: SALESFORCE_CLIENT_SECRET`)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_SECRETISH = re.compile(r"(api_?key|secret|password|token|private_key)$", re.I)
IDENTITY_MODES = ("fake", "none", "service_account", "token_exchange", "ema", "user_oauth")


class ConfigError(ValueError):
    pass


def load_env_file(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines into os.environ (real environment variables win)."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'"))


def interpolate_env(obj: Any, where: str = "") -> Any:
    if isinstance(obj, str):
        def rep(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ConfigError(f"{where or 'config'}: environment variable {name} is not set (and has no default)")
        return _ENV_REF.sub(rep, obj)
    if isinstance(obj, dict):
        return {k: interpolate_env(v, f"{where}.{k}" if where else str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate_env(v, f"{where}[{i}]") for i, v in enumerate(obj)]
    return obj


def reject_inline_secrets(obj: Any, where: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{where}.{k}" if where else str(k)
            if isinstance(k, str) and _SECRETISH.search(k) and isinstance(v, str) and v:
                raise ConfigError(f"{here}: put secrets in the environment and reference them with {k}_env")
            reject_inline_secrets(v, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            reject_inline_secrets(v, f"{where}[{i}]")


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"{path}: not found")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping")
    reject_inline_secrets(data, path.name)
    return interpolate_env(data, path.name)


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = "*"
    deny_if: str | None = None
    require_approval_if: str | None = None
    approver_role: str = "approver"
    message: str | None = None
    rate_limit: dict[str, str] | None = None     # {per_user: "30/min"} or {per_tenant: "1000/hour"}


class AuthSpec(BaseModel):
    """How the hub gets a credential for this system, for the end user it is acting for."""
    model_config = ConfigDict(extra="forbid")
    identity: str = "fake"
    token_url: str = ""                  # token_exchange / user_oauth / service_account (client credentials)
    audience: str = ""                   # token_exchange: the system's audience; ema: the system's authorization server
    resource: str = ""                   # ema: the downstream MCP server URL
    downstream_token_url: str = ""       # ema: the system's authorization server token endpoint
    authorize_url: str = ""              # user_oauth: account linking
    client_id_env: str = ""
    client_secret_env: str = ""
    static_token_env: str = ""           # service_account with an API key / static token
    subject_token_type: str = "urn:ietf:params:oauth:token-type:access_token"
    scopes: dict[str, list[str]] = Field(default_factory=dict)   # per tool; "*" for all
    header: str = "Authorization"
    scheme: str = "Bearer"
    user_filter: str = ""                # service_account: how the pack filters results by user (documented, required)

    def model_post_init(self, _ctx: Any) -> None:
        if self.identity not in IDENTITY_MODES:
            raise ConfigError(f"auth.identity={self.identity!r}: expected one of {', '.join(IDENTITY_MODES)}")

    def scopes_for(self, tool: str) -> list[str]:
        return list(self.scopes.get(tool, self.scopes.get("*", [])))


class PackSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str = "0.1.0"
    description: str = ""
    kind: str = "python"                 # python | openapi
    system: str
    backend: str = "rest"                # which backend class the tools use (mcp | rest | ...)
    backends: dict[str, str] = Field(default_factory=dict)       # name -> "module:Class" in the pack folder
    tools_module: str = "tools"
    settings: dict[str, Any] = Field(default_factory=dict)
    auth: AuthSpec = Field(default_factory=AuthSpec)
    policies: list[PolicySpec] = Field(default_factory=list)
    fakes: dict[str, str] = Field(default_factory=dict)          # fake host -> "module:attr" (ASGI app or MCP server)
    openapi: dict[str, Any] = Field(default_factory=dict)        # kind: openapi
    validation: dict[str, str] = Field(default_factory=dict)     # backend -> implemented | fixture_tested | live_tested | ...
    docs: str = "README.md"


class ProxySpec(BaseModel):
    """Expose selected tools of an upstream MCP server (e.g. a vendor's), renamed and narrowed."""
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str
    system: str
    auth: AuthSpec = Field(default_factory=AuthSpec)
    tools: list[dict[str, Any]] = Field(default_factory=list)   # {upstream, as, description, fixed_args, hide_args, enum, writes}


class ProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    tools: list[str] = Field(default_factory=list)       # "pack:tool" or "pack:*"
    rename: dict[str, str] = Field(default_factory=dict) # "pack:tool" -> exposed name
    allow_writes: bool = True
    policies: list[PolicySpec] = Field(default_factory=list)
    proxy: list[ProxySpec] = Field(default_factory=list)
    tokenize_results: bool = False
    timeout_s: float = 30.0


class HubSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "connector-hub"
    packs_dir: str = "packs"
    profiles_dir: str = "profiles"
    packs: list[str] = Field(default_factory=list)       # enabled packs (folder names); empty = all in packs_dir
    store: str = "sqlite:///.hub/hub.db"
    hooks: dict[str, str] = Field(default_factory=dict)  # on_result / on_audit: "module:function"
    base_dir: Path = Path(".")


def load_hub(path: str | Path) -> HubSpec:
    p = Path(path)
    if p.is_dir():
        p = p / "hub.yaml"
    load_env_file(p.parent / ".env")
    spec = HubSpec(**read_yaml(p))
    spec.base_dir = p.parent.resolve()
    return spec
