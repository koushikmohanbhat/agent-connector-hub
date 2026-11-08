"""OpenAPI 3.x in, governed tools out.

A pack with `kind: openapi` names a spec and the operations to expose:

    kind: openapi
    system: warranty
    openapi:
      spec: warranty.yaml
      base_url: "${WARRANTY_API_URL:-fake://warranty}"
      operations: [getWarranty, listClaims, createClaim]
      allow_writes: [createClaim]        # any POST / PUT / PATCH / DELETE not listed is refused

Each operation becomes a tool: path, query and header parameters become arguments, a JSON request body becomes
`body`. With a fake:// base URL the hub serves each operation's documented example response, so the tools run
offline. `hub generate openapi` writes such a pack from a spec.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import fakes
from .config import ConfigError
from .tools import HubTool, ToolContext, ToolError

WRITE_METHODS = {"post", "put", "patch", "delete"}


def load_spec(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    spec = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not str(spec.get("openapi", "")).startswith("3"):
        raise ConfigError(f"{path}: only OpenAPI 3.x is supported")
    return spec


def _resolve(node: Any, spec: dict, depth: int = 0) -> Any:
    if depth > 20:
        return {}
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                raise ConfigError(f"external $ref {ref!r} is not supported; bundle the spec first")
            target: Any = spec
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return _resolve(target, spec, depth + 1)
        return {k: _resolve(v, spec, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, spec, depth + 1) for v in node]
    return node


def operations(spec: dict) -> dict[str, dict]:
    out = {}
    for path, item in (spec.get("paths") or {}).items():
        common = item.get("parameters", [])
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete") or not isinstance(op, dict):
                continue
            oid = op.get("operationId") or re.sub(r"\W+", "_", f"{method}_{path}").strip("_")
            out[oid] = {"path": path, "method": method, "op": op, "parameters": common + op.get("parameters", [])}
    return out


def _schema_for(o: dict, spec: dict) -> tuple[dict, list[tuple[str, str]]]:
    props: dict[str, Any] = {}
    required: list[str] = []
    where: list[tuple[str, str]] = []
    for p in _resolve(o["parameters"], spec):
        if p.get("in") not in ("path", "query", "header"):
            continue
        s = deepcopy(p.get("schema", {"type": "string"}))
        if "example" in p:
            s.setdefault("example", p["example"])
        if p.get("description"):
            s["description"] = p["description"]
        props[p["name"]] = s
        where.append((p["name"], p["in"]))
        if p.get("required") or p["in"] == "path":
            required.append(p["name"])
    body = _resolve(o["op"].get("requestBody") or {}, spec)
    js = (body.get("content") or {}).get("application/json")
    if js:
        props["body"] = deepcopy(js.get("schema", {"type": "object"}))
        if "example" in js:
            props["body"].setdefault("example", js["example"])
        if body.get("required"):
            required.append("body")
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema, where


def _example(o: dict, spec: dict) -> Any:
    for code, resp in sorted((o["op"].get("responses") or {}).items()):
        if not str(code).startswith("2"):
            continue
        resp = _resolve(resp, spec)
        js = (resp.get("content") or {}).get("application/json") or {}
        if "example" in js:
            return js["example"]
        for ex in (js.get("examples") or {}).values():
            if "value" in ex:
                return ex["value"]
        if "example" in (js.get("schema") or {}):
            return js["schema"]["example"]
    return {"status": "ok", "note": "fake response: the spec has no example for this operation"}


def fake_app(spec: dict):
    """An ASGI app that answers every operation with its documented example (used for fake:// base URLs)."""
    app = FastAPI()
    ops = operations(spec)
    routes = [(re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", o["path"]) + "$"), o) for o in ops.values()]

    @app.api_route("/{full:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def any_route(full: str, request: Request):
        if not request.headers.get("authorization"):
            return JSONResponse({"error": "unauthorized"}, 401)
        for rx, o in routes:
            if rx.match("/" + full) and o["method"] == request.method.lower():
                return JSONResponse(_example(o, spec))
        return JSONResponse({"error": "not found"}, 404)
    return app


def tools_from_spec(pack) -> list[HubTool]:
    cfg = pack.spec.openapi
    if not cfg.get("spec"):
        raise ConfigError(f"{pack.name}: openapi.spec is required")
    spec = load_spec(pack.dir / cfg["spec"])
    ops = operations(spec)
    wanted = cfg.get("operations") or list(ops)
    unknown = [w for w in wanted if w not in ops]
    if unknown:
        raise ConfigError(f"{pack.name}: operations not in the spec: {', '.join(unknown)}")
    base_url = cfg.get("base_url", "")
    if fakes.is_fake(base_url):
        from urllib.parse import urlsplit
        fakes.register(urlsplit(base_url).netloc, fake_app(spec))
    allow_writes = set(cfg.get("allow_writes", []))
    renames = cfg.get("rename", {})
    out = []
    for oid in wanted:
        o = ops[oid]
        schema, where = _schema_for(o, spec)
        writes = o["method"] in WRITE_METHODS
        desc = (o["op"].get("description") or o["op"].get("summary") or f"{o['method'].upper()} {o['path']}").strip()

        async def handler(ctx: ToolContext, args: dict[str, Any], _o=o, _where=where, _writes=writes, _oid=oid) -> Any:
            if _writes and _oid not in allow_writes:
                raise ToolError(f"{_oid} writes to {pack.spec.system} and is not in openapi.allow_writes")
            if not base_url:
                raise ToolError(f"{pack.name}: openapi.base_url is not set")
            path, query, headers = _o["path"], {}, {}
            for name, loc in _where:
                if name not in args or args[name] is None:
                    continue
                if loc == "path":
                    path = path.replace("{" + name + "}", str(args[name]))
                elif loc == "query":
                    query[name] = args[name]
                else:
                    headers[name] = str(args[name])
            token = await ctx.token()
            if token:
                headers[pack.spec.auth.header] = f"{pack.spec.auth.scheme} {token}".strip()
            async with fakes.http_client(base_url, timeout=30.0) as c:
                r = await c.request(_o["method"].upper(), path, params=query, headers=headers,
                                    json=args.get("body") if "body" in args else None)
            if r.status_code >= 400:
                raise ToolError(f"{_oid}: {pack.spec.system} returned {r.status_code}")
            try:
                return r.json()
            except ValueError:
                return {"status": r.status_code, "text": r.text[:2000]}

        out.append(HubTool(renames.get(oid, oid), desc, schema, handler, pack.name, pack.spec.system, writes))
    return out


def generate_pack(spec_path: Path, out_dir: Path, system: str, ops: list[str] | None, base_url_env: str) -> Path:
    spec = load_spec(spec_path)
    all_ops = operations(spec)
    chosen = ops or [k for k, v in all_ops.items() if v["method"] == "get"]
    unknown = [o for o in chosen if o not in all_ops]
    if unknown:
        raise ConfigError(f"not in the spec: {', '.join(unknown)} (it has: {', '.join(all_ops)})")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / spec_path.name).write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
    writes = [o for o in chosen if all_ops[o]["method"] in WRITE_METHODS]
    name = out_dir.name
    env = re.sub(r"\W", "_", system).upper()
    pack = {
        "name": name, "version": "0.1.0", "kind": "openapi", "system": system,
        "description": (spec.get("info") or {}).get("title", system),
        "openapi": {"spec": spec_path.name, "base_url": f"${{{base_url_env}:-fake://{name}}}", "operations": chosen,
                    "allow_writes": []},
        "auth": {"identity": f"${{{env}_IDENTITY:-fake}}", "token_url": f"${{{env}_TOKEN_URL:-}}",
                 "audience": f"${{{env}_AUDIENCE:-}}", "client_id_env": f"{env}_CLIENT_ID",
                 "client_secret_env": f"{env}_CLIENT_SECRET"},
        "validation": {"fake": "fixture_tested", "live": "implemented"},
    }
    (out_dir / "pack.yaml").write_text(
        "# Generated by `hub generate openapi`. Writes are refused until you list them in openapi.allow_writes.\n"
        + yaml.safe_dump(pack, sort_keys=False), encoding="utf-8")
    pol = {"policies": [{"tool": w, "require_approval_if": "True", "approver_role": "approver",
                         "message": f"{w} changes {system}; review before enabling without approval"} for w in writes]}
    (out_dir / "policies.yaml").write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    calls = []
    for o in chosen:
        schema = _schema_for(all_ops[o], spec)[0]
        args = {k: v["example"] for k, v in schema["properties"].items() if "example" in v}
        if all(r in args for r in schema.get("required", [])):
            expect = "ok" if all_ops[o]["method"] == "get" else "pending_approval"
            calls.append({"tool": o, "args": args, "expect": {"status": expect}})
    (out_dir / "conformance.yaml").write_text(yaml.safe_dump({"calls": calls}, sort_keys=False), encoding="utf-8")
    (out_dir / "README.md").write_text(
        f"# {name}\n\nGenerated from `{spec_path.name}` by `hub generate openapi`.\n\n"
        + "\n".join(f"- `{o}` ({all_ops[o]['method'].upper()} {all_ops[o]['path']})" for o in chosen)
        + f"\n\nSet `{base_url_env}` to the API's base URL and `{env}_IDENTITY` to how the hub should authenticate.\n",
        encoding="utf-8")
    return out_dir
